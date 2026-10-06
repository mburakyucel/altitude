"""Real Git candidates and adversarial validation evidence cross the payload boundary."""
import base64
import hashlib
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest
from unittest import mock
import zlib

from altitude import validation_payload as payload
from altitude import platform


class ValidationPayloadTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "tracked.txt").write_text("committed\n")
        self.git("add", ".")
        self.git("commit", "-m", "first")
        self.parent = self.git("rev-parse", "HEAD").strip().decode()
        (self.repo / "run.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.repo / "run.sh").chmod(0o755)
        (self.repo / ".gitattributes").write_text("tracked.txt export-ignore\n")
        self.git("add", ".")
        self.git("commit", "-m", "candidate")

    def git(self, *args, cwd=None, input=None):
        env = {"PATH": os.defpath, "HOME": str(self.root), "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": "/dev/null", "LC_ALL": "C"}
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args], env=env,
                              input=input, check=True, capture_output=True).stdout

    def test_exact_candidate_without_working_edits_history_refs_or_config(self):
        (self.repo / "tracked.txt").write_text("dirty")
        (self.repo / "untracked").write_text("private")
        self.git("config", "remote.origin.url", "file:///private/source")
        self.git("tag", "private-tag", self.parent)
        encoded = payload.export(self.repo)
        destination = self.root / "restored"
        payload.restore(encoded, destination)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=destination).decode().strip(), encoded["commit"])
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}", cwd=destination).decode().strip(), encoded["tree"])
        self.assertEqual(self.git("status", "--porcelain", cwd=destination), b"")
        self.assertEqual((destination / "tracked.txt").read_text(), "committed\n")
        self.assertTrue((destination / "run.sh").stat().st_mode & 0o111)
        self.assertFalse((destination / "untracked").exists())
        self.assertEqual(self.git("rev-list", "--count", "HEAD", cwd=destination), b"1\n")
        self.assertEqual(self.git("tag", cwd=destination), b"")
        self.assertEqual(self.git("remote", cwd=destination), b"")
        self.assertFalse((destination / ".git/hooks").exists())
        self.assertFalse((destination / ".git/objects/info/alternates").exists())
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("cat-file", "-e", self.parent, cwd=destination)

    def test_inherited_git_injections_are_ignored(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": "/missing", "GIT_CONFIG_COUNT": "1",
                                         "GIT_CONFIG_KEY_0": "core.repositoryformatversion",
                                         "GIT_CONFIG_VALUE_0": "100"}):
            self.assertEqual(payload.export(self.repo)["commit"], self.git("rev-parse", "HEAD").decode().strip())

    def test_export_pins_tree_when_head_moves_after_commit_read(self):
        commit = self.git("rev-parse", "HEAD").decode().strip()
        tree = self.git("rev-parse", "HEAD^{tree}").decode().strip()
        real_git = payload._git

        def moving_head(worktree, *args, **kwargs):
            result = real_git(worktree, *args, **kwargs)
            if args == ("rev-parse", "--verify", "HEAD^{commit}"):
                self.git("update-ref", "HEAD", self.parent)
            return result

        with mock.patch.object(payload, "_git", side_effect=moving_head):
            exported = payload.export(self.repo)
        self.assertEqual(exported["commit"], commit)
        self.assertEqual(exported["tree"], tree)
        destination = self.root / "restored"
        payload.restore(exported, destination)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=destination).decode().strip(), commit)
        self.assertTrue((destination / "run.sh").exists())
        self.assertEqual(self.git("rev-parse", "HEAD").decode().strip(), self.parent)

    def test_export_deadline_is_shared_and_prevents_another_git_process(self):
        clock = [0.0]
        real_git = payload._git
        deadlines = []

        def elapsed_read(worktree, *args, **kwargs):
            deadlines.append(kwargs.get("deadline"))
            result = real_git(worktree, *args, **kwargs)
            clock[0] += 21  # Each read is within its own timeout; together they exceed it.
            return result

        with mock.patch.object(payload, "time", mock.Mock(monotonic=lambda: clock[0])), \
                mock.patch.object(payload, "_git", side_effect=elapsed_read), \
                mock.patch.object(payload.subprocess, "Popen", wraps=subprocess.Popen) as processes:
            with self.assertRaisesRegex(ValueError, "timed out"):
                payload.export(self.repo)
        self.assertEqual(deadlines, [payload.EXPORT_TIMEOUT] * 4)
        self.assertEqual(processes.call_count, 3)

    def test_digest_and_identity_tampering_refused_before_writes(self):
        original = payload.export(self.repo)
        document = payload._unpack(original)
        blob = next(oid for oid, value in document["objects"].items() if value[0] == "blob")
        document["objects"][blob][1] = base64.b64encode(b"tampered").decode()
        bad_object = {**original, **payload._pack(document)}
        for index, changed in enumerate([
            {**original, "digest": "0" * 64},
            {**original, "commit": "0" * 40},
            {**original, "tree": "0" * 40}, bad_object,
        ]):
            destination = self.root / f"bad-{index}"
            with self.assertRaises(ValueError):
                payload.restore(changed, destination)
            self.assertFalse(destination.exists())

    def test_missing_and_unrelated_objects_are_refused(self):
        original = payload.export(self.repo)
        for missing in [True, False]:
            document = payload._unpack(original)
            if missing:
                del document["objects"][original["tree"]]
            else:
                document["objects"][self.parent] = ["commit", base64.b64encode(self.git("cat-file", "commit", self.parent)).decode()]
            with self.assertRaises(ValueError):
                payload.restore({**original, **payload._pack(document)}, self.root / "bad")

    def test_git_symlinks_and_submodules_are_refused(self):
        (self.repo / "link").symlink_to("tracked.txt")
        self.git("add", "link")
        self.git("commit", "-m", "link")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            payload.export(self.repo)
        self.git("rm", "link")
        self.git("update-index", "--add", "--cacheinfo", f"160000,{self.parent},module")
        self.git("commit", "-m", "module")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            payload.export(self.repo)

    def test_unsafe_raw_tree_names_and_case_collisions_are_refused(self):
        blob = self.git("hash-object", "-w", "--stdin", input=b"safe").decode().strip()
        for names in [["../escape"], [".git"], [".GIT"], ["a\\b"], ["/absolute"], ["A", "a"]]:
            tree = b"".join(b"100644 " + name.encode() + b"\0" + bytes.fromhex(blob) for name in names)
            tree_id = payload._identity("tree", tree)
            commit = b"tree " + tree_id.encode() + b"\nauthor Fixture <f@example.invalid> 1 +0000\ncommitter Fixture <f@example.invalid> 1 +0000\n\nfixture\n"
            commit_id = payload._identity("commit", commit)
            document = {"objects": {tree_id: ["tree", payload._b64(tree)],
                                    commit_id: ["commit", payload._b64(commit)],
                                    blob: ["blob", payload._b64(b"safe")]}}
            with self.assertRaises(ValueError):
                payload.restore({"commit": commit_id, "tree": tree_id, **payload._pack(document)}, self.root / "bad")

    def test_payload_and_expansion_budgets_and_truncation(self):
        original = payload.export(self.repo)
        data = base64.b64decode(original["data"])
        for raw in [data[:-1], data + b"trailing"]:
            changed = {**original, "data": payload._b64(raw), "digest": hashlib.sha256(raw).hexdigest()}
            with self.assertRaises(ValueError):
                payload.restore(changed, self.root / "bad")
        with mock.patch.object(payload, "COMPRESSED_LIMIT", 8):
            with self.assertRaises(ValueError):
                payload.export(self.repo)
            with self.assertRaises(ValueError):
                payload.restore(original, self.root / "bad")
        bomb = zlib.compress(b" " * 10000)
        with mock.patch.object(payload, "EXPANDED_LIMIT", 100):
            with self.assertRaises(ValueError):
                payload._unpack({"data": payload._b64(bomb), "digest": hashlib.sha256(bomb).hexdigest()})

    def test_repeated_blob_cannot_expand_past_working_file_budget(self):
        raw = b"x" * 1000
        blob = payload._identity("blob", raw)
        tree = b"".join(b"100644 file-" + str(index).zfill(3).encode() + b"\0" + bytes.fromhex(blob)
                        for index in range(20))
        tree_id = payload._identity("tree", tree)
        commit = b"tree " + tree_id.encode() + b"\n\nfixture\n"
        commit_id = payload._identity("commit", commit)
        objects = {blob: raw, tree_id: tree, commit_id: commit}
        with mock.patch.object(payload, "EXPANDED_LIMIT", 5000):
            with self.assertRaisesRegex(ValueError, "working file budget"):
                payload._candidate(commit_id, tree_id, lambda oid, kind: objects[oid])

    def test_tracked_empty_tree_is_preserved(self):
        empty_tree = self.git("hash-object", "-w", "-t", "tree", "--stdin", input=b"").decode().strip()
        raw_tree = b"40000 empty\0" + bytes.fromhex(empty_tree)
        tree = self.git("hash-object", "-w", "-t", "tree", "--stdin", input=raw_tree).decode().strip()
        commit = self.git("commit-tree", tree, "-m", "empty subtree").decode().strip()
        self.git("update-ref", "HEAD", commit)
        destination = self.root / "restored"
        payload.restore(payload.export(self.repo), destination)
        self.assertTrue((destination / "empty").is_dir())
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}", cwd=destination).decode().strip(), tree)

    def results(self):
        source = self.root / "results"
        source.mkdir()
        (source / "empty").mkdir()
        (source / "screenshots").mkdir()
        (source / "screenshots/phone.png").write_bytes(b"fictional image\0")
        (source / "stdout.log").write_text("passed\n")
        return source

    def test_results_round_trip_includes_empty_directories(self):
        source = self.results()
        destination = self.root / "evidence"
        payload.restore_results(payload.collect_results(source), destination)
        self.assertTrue((destination / "empty").is_dir())
        self.assertEqual((destination / "screenshots/phone.png").read_bytes(), b"fictional image\0")
        self.assertEqual((destination / "stdout.log").read_text(), "passed\n")

    def test_result_links_and_special_files_are_refused_without_path_disclosure(self):
        source = self.results()
        special = source / "special"
        for kind in ["symlink", "hardlink", "fifo", "directory-link"]:
            if kind == "symlink":
                special.symlink_to(source / "stdout.log")
            elif kind == "hardlink":
                os.link(source / "stdout.log", special)
            elif kind == "fifo":
                os.mkfifo(special)
            else:
                special.symlink_to(source / "screenshots", target_is_directory=True)
            with self.assertRaises(ValueError) as caught:
                payload.collect_results(source)
            self.assertNotIn(str(source), str(caught.exception))
            special.unlink()

    def test_result_inventory_requires_safe_complete_paths_and_regular_files(self):
        cases = [
            {"../escape": ["file", ""]}, {".git/config": ["file", ""]},
            {"a/file": ["file", ""]}, {"a": ["file", ""], "a/file": ["file", ""]},
            {"a": ["symlink", "outside"]}, {"a": ["file", "not-base64!"]},
            {"a": ["dir"], "A": ["file", ""]},
        ]
        for index, entries in enumerate(cases):
            destination = self.root / f"bad-{index}"
            with self.assertRaises(ValueError):
                payload.restore_results({"entries": entries, "digest": payload._results_digest(entries)}, destination)
            self.assertFalse(destination.exists())

    def test_result_file_total_entry_limits_and_existing_destination(self):
        source = self.results()
        original = payload.collect_results(source)
        for constant, value in [("FILE_LIMIT", 2), ("RESULT_LIMIT", 20), ("ENTRY_LIMIT", 1)]:
            with mock.patch.object(payload, constant, value):
                with self.assertRaises(ValueError):
                    payload.collect_results(source)
                with self.assertRaises(ValueError):
                    payload.restore_results(original, self.root / "bad")
        with self.assertRaises(ValueError):
            payload.restore_results(original, source)
        link = self.root / "link"
        link.symlink_to(self.root / "missing")
        with self.assertRaises(ValueError):
            payload.restore_results(original, link)

    def test_result_raw_budget_does_not_include_base64_or_source_limits(self):
        source = self.root / "results"
        source.mkdir()
        raw = random.Random(7).randbytes(1024)
        (source / "binary").write_bytes(raw)
        with mock.patch.object(payload, "RESULT_LIMIT", len(raw)), \
                mock.patch.object(payload, "COMPRESSED_LIMIT", 32), \
                mock.patch.object(payload, "EXPANDED_LIMIT", 64):
            result = payload.collect_results(source)
            destination = self.root / "restored"
            payload.restore_results(result, destination)
            self.assertEqual((destination / "binary").read_bytes(), raw)
            (source / "one-too-many").write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "size limit"):
                payload.collect_results(source)
            entries = {**result["entries"], "one-too-many": ["file", payload._b64(b"x")]}
            with self.assertRaisesRegex(ValueError, "size limit"):
                payload.restore_results({"entries": entries, "digest": payload._results_digest(entries)},
                                        self.root / "overflow")

    def test_large_incompressible_result_and_frame_round_trip(self):
        source = self.root / "results"
        source.mkdir()
        block = random.Random(42).randbytes(1 << 20)
        path = source / "trace.zip"
        with path.open("wb") as stream:
            for _ in range(70):
                stream.write(block)
        result = payload.collect_results(source)
        self.assertGreater(path.stat().st_size, payload.COMPRESSED_LIMIT)
        # Exercise the actual framed JSON representation, with no compression assumption.
        import io
        framed = platform.validation_frame_encode({"status": "finished", "evidence": result})
        decoded = platform.validation_frame_read(io.BytesIO(framed))["evidence"]
        self.assertLess(len(framed), platform.VALIDATION_FRAME_LIMIT)
        del framed, result
        destination = self.root / "restored"
        payload.restore_results(decoded, destination)
        with path.open("rb") as original, (destination / "trace.zip").open("rb") as restored:
            self.assertEqual(hashlib.file_digest(original, "sha256").digest(),
                             hashlib.file_digest(restored, "sha256").digest())

    def test_maximum_result_budget_has_proven_frame_headroom(self):
        # Separate base64 encodings add at most four padding bytes per file; all path
        # characters are single-byte JSON-safe ASCII, with under 40 bytes of entry syntax.
        worst_inventory = 4 * ((payload.RESULT_LIMIT + 2) // 3) + payload.ENTRY_LIMIT * (payload.PATH_LIMIT + 40) + 1024
        self.assertLess(worst_inventory, payload.RESULT_JSON_LIMIT)
        self.assertLess(payload.RESULT_JSON_LIMIT + (1 << 20), platform.VALIDATION_FRAME_LIMIT)

    def test_result_digest_and_json_budget_are_checked_before_writes(self):
        source = self.results()
        result = payload.collect_results(source)
        result["entries"]["stdout.log"][1] = payload._b64(b"altered log")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            payload.restore_results(result, self.root / "bad")
        self.assertFalse((self.root / "bad").exists())
        with mock.patch.object(payload, "RESULT_JSON_LIMIT", 1):
            with self.assertRaisesRegex(ValueError, "JSON size limit"):
                payload.collect_results(source)
            with self.assertRaisesRegex(ValueError, "JSON size limit"):
                payload.restore_results(result, self.root / "bad")

    def test_protected_summaries_survive_invalid_artifacts_with_strict_selection(self):
        source = self.root / "results"
        source.mkdir()
        (source / "output.log").write_text("observed output\n")
        (source / "receipt.json").write_text('{"exit":null,"ended":"unavailable"}')
        (source / "artifacts").symlink_to(self.root)
        with self.assertRaises(ValueError):
            payload.collect_results(source)
        result = payload.collect_results(source, names=("output.log", "receipt.json"))
        self.assertEqual(set(result["entries"]), {"output.log", "receipt.json"})
        destination = self.root / "restored"
        payload.restore_results(result, destination)
        self.assertIn("observed output", (destination / "output.log").read_text())
        for names in [("output.log",), ("output.log", "../escape"), ("output.log", "artifacts")]:
            with self.assertRaises(ValueError):
                payload.collect_results(source, names=names)
        (source / "receipt.json").unlink()
        with self.assertRaises(ValueError):
            payload.collect_results(source, names=("output.log", "receipt.json"))
        (source / "receipt.json").write_bytes(b"x" * 65537)
        with self.assertRaises(ValueError):
            payload.collect_results(source, names=("output.log", "receipt.json"))


if __name__ == "__main__":
    unittest.main()
