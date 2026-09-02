"""Phase 0B runtime identity and copied-home preflight are deterministic and read-only."""
from __future__ import annotations

import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if "ALTITUDE_HOME" not in os.environ:
    os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-manifest-import-")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from altitude import legacy_preflight, manifest  # noqa: E402


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _snapshot(root: Path) -> list[tuple[str, str, int]]:
    rows = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows.append((rel, f"link:{os.readlink(path)}", path.lstat().st_mode))
        elif path.is_file():
            rows.append((rel, manifest._sha256(path.read_bytes()), path.stat().st_mode))
        else:
            rows.append((rel + "/", "", path.stat().st_mode))
    return rows


class TestRuntimeManifest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="altitude-phase0b-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo = self._repo()
        (self.root / "state").mkdir()
        _write_json(self.root / "state" / "projects.json", {})
        self.installed_unit = self.root / "installed-altitude.service"
        self.installed_unit.write_bytes((self.repo / "systemd" / "altitude.service").read_bytes())
        provider_units = mock.patch.object(
            manifest, "_manager_provider_units", return_value={"units": [], "error": None})
        provider_units.start()
        self.addCleanup(provider_units.stop)

    def _repo(self) -> Path:
        repo = self.root / "checkout"
        files = {
            "altitude/__init__.py": '__version__ = "test-build"\n',
            "altitude/actions.py": (
                "import os, tempfile\n"
                "def writes(path):\n"
                "    path.chmod(0o600)\n"
                "    fd, name = tempfile.mkstemp()\n"
                "    os.fdopen(fd, 'w').write('x')\n"
                "    tempfile.NamedTemporaryFile()\n"
                "    os.open(name, os.O_WRONLY | os.O_CREAT)\n"
            ),
            "altitude/server.py": 'if parts[2] == "add":\n    pass\n',
            "bin/alt": "#!/usr/bin/env python3\n",
            "scripts/restart_altitude.py": "# restart\n",
            "personas/l2.md": "owner\n",
            "schemas/report.json": "{}\n",
            "templates/brief.md": "brief\n",
            "hooks/guard.py": "# guard\n",
            "systemd/altitude.service": "[Service]\nExecStart=bin/alt serve\n",
            "web/dist/index.html": "<div id=root></div>\n",
        }
        rosters = (legacy_preflight.PERMANENT_BACKEND_ROSTER,
                   legacy_preflight.WEB_SOURCE_ROSTER, legacy_preflight.WEB_BUILD_ROSTER,
                   legacy_preflight.PERSONA_SCHEMA_TEMPLATE_ROSTER,
                   legacy_preflight.SUPPORT_ROSTER, legacy_preflight.TEMPORARY_ROSTER)
        for name in {name for roster in rosters for name in roster}:
            if name in files:
                continue
            if name.endswith(".py") or name in ("bin/alt", *legacy_preflight.PERMANENT_BACKEND_ROSTER[27:31]):
                files[name] = "# fixture\n"
            elif name.endswith(".json"):
                files[name] = "{}\n"
            else:
                files[name] = "fixture\n"
        for name, contents in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        commands = (
            ("git", "init", "-q", str(repo)),
            ("git", "-C", str(repo), "config", "user.name", "Manifest Test"),
            ("git", "-C", str(repo), "config", "user.email", "manifest@example.invalid"),
            ("git", "-C", str(repo), "add", "."),
            ("git", "-C", str(repo), "commit", "-qm", "fixture"),
            ("git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", "HEAD"),
        )
        for command in commands:
            subprocess.run(command, check=True, capture_output=True)
        return repo

    def _manager(self) -> dict:
        observed = {
            "fragment_path": str(self.installed_unit),
            "drop_in_paths": [],
            "exec_start": f"{{{self.repo / 'bin' / 'alt'} ; argv[]={self.repo / 'bin' / 'alt'} serve ;}}",
            "load_state": "loaded", "active_state": "active", "sub_state": "running",
            "unit_file_state": "enabled", "need_daemon_reload": "no",
            "main_pid": 4242, "control_group": "/test.slice/altitude.service",
            "process": {"pid": 4242, "start_ticks": 99123, "error": None},
        }
        observed["cgroup"] = {"control_group": observed["control_group"], "present": True,
                              "pids": [4242], "empty": False, "error": None}
        configuration = {key: observed[key] for key in
                         ("fragment_path", "drop_in_paths", "exec_start", "load_state",
                          "unit_file_state", "need_daemon_reload")}
        return {"observed": True,
                "sha256": manifest._sha256(json.dumps(observed, sort_keys=True).encode()),
                "configuration_sha256": manifest._sha256(
                    json.dumps(configuration, sort_keys=True).encode()),
                **observed, "error": None}

    def _stopped_manager(self) -> dict:
        value = self._manager() | {
            "active_state": "inactive", "sub_state": "dead", "main_pid": 0,
            "process": {"pid": 0, "start_ticks": None, "error": None},
            "control_group": "", "cgroup": {
                "control_group": "", "present": False, "pids": [], "empty": True, "error": None,
            },
        }
        observed = {key: value[key] for key in
                    ("fragment_path", "drop_in_paths", "exec_start", "load_state", "active_state",
                     "sub_state", "unit_file_state", "need_daemon_reload", "main_pid",
                     "control_group", "cgroup", "process")}
        value["sha256"] = manifest._sha256(json.dumps(observed, sort_keys=True).encode())
        return value

    def _startup_source(self) -> dict:
        source = manifest._selected_tree_identity(self.repo, manifest._runtime_source_files(self.repo))
        rows = [{key: row[key] for key in ("path", "mode", "sha256", "stat")}
                for row in source["files"]]
        return {"root": str(self.repo), "files": rows,
                "sha256": manifest._sha256(json.dumps(
                    rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())}

    def _loaded_modules(self) -> dict:
        return {"altitude": str(self.repo / "altitude" / "__init__.py"),
                "altitude.config": str(self.repo / "altitude" / "config.py"),
                "altitude.manifest": str(self.repo / "altitude" / "manifest.py")}

    def _bootstrap_evidence(self, installation: dict, state_home: Path) -> tuple[dict, bytes]:
        state_sha = manifest._tree_identity(state_home)["sha256"]
        raw_value = {
            "schema_version": manifest.RAW_FREEZE_EVIDENCE_SCHEMA,
            "observed_at": "2026-09-02T00:00:00+00:00",
            "loaded_source": {
                "commit": installation["source_commit"],
                "source_sha256": installation["source_sha256"],
                "entry_sha256": installation["entry_sha256"],
            },
            "service": {
                "unit": "altitude.service",
                "fragment_sha256": installation["installed_fragment"]["sha256"],
                "configuration_sha256": installation["manager_configuration_sha256"],
                "last_main_pid": 4242, "last_start_ticks": 99123,
                "control_group": "/test.slice/altitude.service", "final_pids": [], "empty": True,
            },
            "state": {"home": str(state_home), "sha256": state_sha},
            "provider_units": [],
            "worktrees": [], "refs": [],
            "processes": [{
                "kind": "service", "provider": None, "pid": 4242, "start_ticks": 99123,
                "unit": "altitude.service", "worker_id": None, "session_id": None, "stopped": True,
            }],
        }
        raw = (json.dumps(raw_value, sort_keys=True) + "\n").encode()
        evidence = {
            "schema_version": manifest.BOOTSTRAP_EVIDENCE_SCHEMA,
            "raw_evidence_sha256": manifest._sha256(raw), "state_sha256": state_sha,
            "source_commit": installation["source_commit"],
            "source_sha256": installation["source_sha256"],
            "entry_sha256": installation["entry_sha256"],
            "repository_unit_sha256": installation["repository_unit_sha256"],
            "web_bundle_sha256": installation["web_bundle_sha256"],
            "manager_configuration_sha256": installation["manager_configuration_sha256"],
            "installed_fragment": installation["installed_fragment"],
            "installed_drop_ins": installation["installed_drop_ins"],
            "last_observed_pid": 4242, "process_empty": True,
            "unavailable_running_facts": sorted(manifest._BOOTSTRAP_UNAVAILABLE),
            "operator_authorization": {
                "id": "first-activation", "actor": "operator", "at": "2026-09-02T00:00:00+00:00",
                "reason": "one-time activation of the already stopped frozen install",
            },
        }
        return evidence, raw

    def _manifest(self, **kwargs) -> dict:
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._manager()):
            return manifest.runtime_manifest(
                role=manifest.RUNNING_INSTALL, repo=self.repo, state_home=self.root / "state",
                entrypoint=str(self.repo / "bin" / "alt"),
                startup_source=self._startup_source(), loaded_modules=self._loaded_modules(), **kwargs)

    def _home(self, name: str = "copied-home") -> Path:
        home = self.root / name
        _write_json(home / "projects.json", {"demo": {"path": str(self.repo)}})
        (home / "demo").mkdir()
        return home

    def _task(self, home: Path, namespace: str, slug: str, state: str, **extra) -> Path:
        directory = home / "demo" / namespace / slug
        value = {"slug": slug, "title": slug, "state": state, "agent_id": None,
                 "session_id": None, "dispatch_id": None, "l2_engine": None, **extra}
        _write_json(directory / "status.json", value)
        return directory

    def _preflight(self, home: Path, **kwargs) -> dict:
        kwargs.setdefault("provider_home", self.root / "empty-provider-home")
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._manager()):
            return legacy_preflight.offline_preflight(
                home, repo=self.repo, manifest_role=manifest.RUNNING_INSTALL,
                startup_source=self._startup_source(), loaded_modules=self._loaded_modules(), **kwargs)

    def test_manifest_separates_checkout_template_installed_unit_manager_and_bundle(self):
        first = self._manifest()
        second = self._manifest()
        self.assertEqual(first, second)
        self.assertEqual(first["source_commit"], first["loaded_source"]["git_head"])
        self.assertEqual(first["installed_checkout"]["path"], str(self.repo))
        source_paths = {row["path"] for row in first["loaded_source"]["files"]}
        self.assertTrue({"altitude/actions.py", "bin/alt", "scripts/restart_altitude.py",
                         "personas/l2.md", "schemas/report.json", "templates/brief.md",
                         "hooks/guard.py"}.issubset(source_paths))
        unit = first["service_unit"]
        self.assertEqual(unit["repository_template"]["sha256"], unit["installed_fragment"]["sha256"])
        self.assertEqual(unit["installed_fragment"]["path"], str(self.installed_unit))
        self.assertTrue(unit["manager"]["observed"])
        self.assertEqual(first["web_bundle"]["files"], 1)
        self.assertFalse(first["identity_errors"], first["identity_errors"])

    def test_manifest_roles_are_closed_and_running_requires_pid_cgroup_and_exact_exec(self):
        with self.assertRaises(ValueError):
            manifest.runtime_manifest(role="current")
        for manager_row, field in (
            (self._manager() | {"cgroup": {"error": None, "empty": True, "pids": []}},
             "running_process:"),
            (self._manager() | {"process": {"pid": 4242, "start_ticks": None,
                                             "error": "unavailable"}}, "running_process:"),
            (self._manager() | {
                "exec_start": f"{{{self.repo / 'bin' / 'alt'} ; argv[]={self.repo / 'bin' / 'alt'} destroy ;}}"
            }, "manager_runtime_entry:"),
        ):
            with mock.patch.object(manifest, "_manager_unit_identity", return_value=manager_row):
                result = manifest.runtime_manifest(
                    role=manifest.RUNNING_INSTALL, repo=self.repo,
                    entrypoint=str(self.repo / "bin" / "alt"))
            self.assertFalse(result["valid"])
            self.assertTrue(any(error.startswith(field) for error in result["identity_errors"]))

    def test_manager_substate_and_unit_file_state_domains_are_closed(self):
        for manager_row, fragment in (
            (self._manager() | {"sub_state": "future-substate"}, "manager SubState"),
            (self._manager() | {"unit_file_state": "future-state"}, "UnitFileState"),
        ):
            with self.subTest(fragment=fragment), mock.patch.object(
                    manifest, "_manager_unit_identity", return_value=manager_row):
                result = manifest.runtime_manifest(
                    role=manifest.RUNNING_INSTALL, repo=self.repo,
                    entrypoint=str(self.repo / "bin" / "alt"),
                    startup_source=self._startup_source(), loaded_modules=self._loaded_modules())
            self.assertFalse(result["valid"])
            self.assertTrue(any(fragment in error for error in result["identity_errors"]),
                            result["identity_errors"])

    def test_stopped_install_matches_immutable_running_receipt_and_detects_drift(self):
        running = self._manifest()
        self.assertEqual(running["prior_kind"], "ordinary_running_install")
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._stopped_manager()):
            stopped = manifest.runtime_manifest(
                role=manifest.STOPPED_INSTALL, repo=self.repo, prior_receipt=running)
        self.assertTrue(stopped["valid"], stopped["identity_errors"])
        self.assertEqual(stopped["prior_install_manifest_sha256"], running["manifest_sha256"])
        tampered = dict(running); tampered["manifest_sha256"] = "0" * 64
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._stopped_manager()):
            refused = manifest.runtime_manifest(
                role=manifest.STOPPED_INSTALL, repo=self.repo, prior_receipt=tampered)
        self.assertFalse(refused["valid"])
        self.assertTrue(any("hash does not match" in error for error in refused["identity_errors"]))
        populated = self._stopped_manager() | {
            "cgroup": {"control_group": "/old", "present": True, "pids": [99],
                       "empty": False, "error": None},
        }
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=populated):
            not_empty = manifest.runtime_manifest(
                role=manifest.STOPPED_INSTALL, repo=self.repo, prior_receipt=running)
        self.assertFalse(not_empty["valid"])
        self.assertTrue(any(error.startswith("stopped_process:")
                            for error in not_empty["identity_errors"]))
        self.installed_unit.write_text(self.installed_unit.read_text() + "# drift\n")
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._stopped_manager()):
            drifted = manifest.runtime_manifest(
                role=manifest.STOPPED_INSTALL, repo=self.repo, prior_receipt=running)
        self.assertFalse(drifted["valid"])
        self.assertTrue(any("installed_fragment" in error for error in drifted["identity_errors"]))

    def test_first_activation_bootstrap_is_stopped_evidence_never_backdated_running(self):
        observed = self._manifest()["installation_identity"]
        state_home = self.root / "state"
        evidence, raw = self._bootstrap_evidence(observed, state_home)
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._stopped_manager()):
            bootstrap = manifest.runtime_manifest(
                role=manifest.STOPPED_INSTALL, repo=self.repo, state_home=state_home,
                bootstrap_evidence=evidence, bootstrap_raw_evidence=raw)
            revalidated = manifest.runtime_manifest(
                role=manifest.STOPPED_INSTALL, repo=self.repo, prior_receipt=bootstrap)
        self.assertTrue(bootstrap["valid"], bootstrap["identity_errors"])
        self.assertEqual(bootstrap["role"], "stopped_install")
        self.assertEqual(bootstrap["prior_kind"], "bootstrap_freeze")
        self.assertNotEqual(bootstrap["role"], "running_install")
        self.assertTrue(revalidated["valid"], revalidated["identity_errors"])
        bad = dict(evidence); bad.pop("state_sha256")
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._stopped_manager()):
            refused = manifest.runtime_manifest(
                role=manifest.STOPPED_INSTALL, repo=self.repo, state_home=state_home,
                bootstrap_evidence=bad, bootstrap_raw_evidence=raw)
        self.assertFalse(refused["valid"])
        self.assertTrue(any(error.startswith("bootstrap_evidence:") for error in refused["identity_errors"]))

    def test_bootstrap_binds_exact_raw_bytes_state_bytes_and_closed_freeze_rosters(self):
        state_home = self.root / "state"
        installation = self._manifest()["installation_identity"]
        evidence, raw = self._bootstrap_evidence(installation, state_home)

        def stopped(declaration, raw_bytes):
            with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._stopped_manager()):
                return manifest.runtime_manifest(
                    role=manifest.STOPPED_INSTALL, repo=self.repo, state_home=state_home,
                    bootstrap_evidence=declaration, bootstrap_raw_evidence=raw_bytes)

        self.assertTrue(stopped(evidence, raw)["valid"])
        self.assertTrue(any("bytes do not match" in error for error in
                            stopped(evidence, raw + b" ")["identity_errors"]))
        state_home.joinpath("drift.json").write_text("{}\n")
        self.assertTrue(any("copied state" in error for error in
                            stopped(evidence, raw)["identity_errors"]))
        state_home.joinpath("drift.json").unlink()

        raw_value = json.loads(raw)
        raw_value.pop("refs")
        bad_raw = (json.dumps(raw_value, sort_keys=True) + "\n").encode()
        bad_declaration = {**evidence, "raw_evidence_sha256": manifest._sha256(bad_raw)}
        self.assertTrue(any("refs" in error for error in
                            stopped(bad_declaration, bad_raw)["identity_errors"]))
        raw_value = json.loads(raw)
        raw_value["provider_units"].append({
            "provider": "codex", "unit": "altitude-l2-test.scope",
            "active_state": "inactive", "sub_state": "dead", "control_group": "",
            "final_pids": [], "empty": True,
        })
        bad_raw = (json.dumps(raw_value, sort_keys=True) + "\n").encode()
        bad_declaration = {**evidence, "raw_evidence_sha256": manifest._sha256(bad_raw)}
        self.assertTrue(any("provider_units" in error for error in
                            stopped(bad_declaration, bad_raw)["identity_errors"]))

        raw_value = json.loads(raw)
        raw_value["processes"][0]["pid"] = 9999
        bad_raw = (json.dumps(raw_value, sort_keys=True) + "\n").encode()
        bad_declaration = {**evidence, "raw_evidence_sha256": manifest._sha256(bad_raw)}
        self.assertTrue(any("process roster" in error for error in
                            stopped(bad_declaration, bad_raw)["identity_errors"]))
        subprocess.run(("git", "-C", str(self.repo), "update-ref",
                        "refs/heads/worktree-orphan", "HEAD"), check=True)
        self.assertTrue(any("orphaned v1 task refs" in error for error in
                            stopped(evidence, raw)["identity_errors"]))
        with mock.patch.object(manifest, "_manager_provider_units",
                               return_value={"units": ["altitude-codex-orphan.scope"],
                                             "error": None}):
            self.assertTrue(any("provider units" in error for error in
                                stopped(evidence, raw)["identity_errors"]))

    def test_bootstrap_blocks_every_active_v1_task_worker_helper_and_effect(self):
        installation = self._manifest()["installation_identity"]
        state_home = self.root / "bootstrap-active"
        _write_json(state_home / "projects.json", {"demo": {"path": str(self.repo)}})
        task = self._task(state_home, "tasks", "queued", "queued",
                          pending_action={"future": "opaque"})
        _write_json(task / "l1" / "review.json", {"name": "review", "done": None})
        _write_json(task / "l2-engine" / "worker.json", {"id": "worker", "stopped": None})
        _write_json(state_home / "demo" / "l3-actions" / "action.json",
                    {"id": "action", "status": "applying"})
        _write_json(state_home / "monitor" / "restart-pending.json", {"since": "now"})
        evidence, raw = self._bootstrap_evidence(installation, state_home)
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._stopped_manager()):
            result = manifest.runtime_manifest(
                role=manifest.STOPPED_INSTALL, repo=self.repo, state_home=state_home,
                bootstrap_evidence=evidence, bootstrap_raw_evidence=raw)
        reasons = "\n".join(result["identity_errors"])
        for fragment in ("active v1 task", "pending v1 task effect", "active v1 helper",
                         "active v1 worker", "pending v1 action", "operational effect"):
            self.assertIn(fragment, reasons)

    def test_monitor_nested_rate_limit_and_live_agent_shapes_are_closed(self):
        home = self._home("closed-monitor-nested")
        _write_json(home / "monitor" / "statusline-1.json", {
            "_at": 1, "rate_limits": {
                "five_hour": {"used_percentage": 1, "resets_at": "later", "authority": True},
                "seven_day": {"used_percentage": 2, "resets_at": "later"},
            },
        })
        _write_json(home / "monitor" / "live-demo--task.json", {
            "at": "now", "agent": {"status": "busy", "state": "working",
                                       "engine": "claude", "pid": 12,
                                       "usage": {"input_tokens": 1, "authority": True},
                                       "authority": True}, "idle_since": None,
        })
        issues = self._preflight(home)["unknown_active_shapes"]
        self.assertTrue(any("unknown statusline five_hour window fields" in row["reason"]
                            for row in issues), issues)
        self.assertTrue(any("unknown live-cache agent fields" in row["reason"]
                            for row in issues), issues)
        self.assertTrue(any("unknown live-cache agent usage fields" in row["reason"]
                            for row in issues), issues)

    def test_statusline_root_and_known_nested_objects_are_closed(self):
        home = self._home("closed-statusline-root")
        _write_json(home / "monitor" / "statusline-1.json", {
            "_at": 1, "future_authority": True,
            "model": {"id": "claude", "display_name": "Claude", "future": True},
        })
        issues = self._preflight(home)["unknown_active_shapes"]
        self.assertTrue(any("unknown monitor record fields" in row["reason"]
                            for row in issues), issues)
        self.assertTrue(any("unknown statusline model fields" in row["reason"]
                            for row in issues), issues)

    def test_running_identity_requires_unchanged_preimport_bytes_and_loaded_module_origins(self):
        valid = self._manifest()
        self.assertTrue(valid["executed_source"]["valid"])
        self.assertEqual(valid["state"]["sha256"], manifest._tree_identity(self.root / "state")["sha256"])
        early = self._startup_source()
        early["files"][0]["sha256"] = "0" * 64
        early["sha256"] = manifest._sha256(json.dumps(
            early["files"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._manager()):
            drifted = manifest.runtime_manifest(
                role=manifest.RUNNING_INSTALL, repo=self.repo, state_home=self.root / "state",
                entrypoint=str(self.repo / "bin" / "alt"), startup_source=early,
                loaded_modules=self._loaded_modules())
            wrong_origin = manifest.runtime_manifest(
                role=manifest.RUNNING_INSTALL, repo=self.repo, state_home=self.root / "state",
                entrypoint=str(self.repo / "bin" / "alt"), startup_source=self._startup_source(),
                loaded_modules={**self._loaded_modules(), "altitude.server": "/tmp/not-captured.py"})
            missing = manifest.runtime_manifest(
                role=manifest.RUNNING_INSTALL, repo=self.repo, state_home=self.root / "state",
                entrypoint=str(self.repo / "bin" / "alt"))
        self.assertFalse(drifted["valid"])
        self.assertFalse(wrong_origin["valid"])
        self.assertFalse(missing["valid"])
        self.assertTrue(all(any(error.startswith("executed_source:")
                                     for error in row["identity_errors"])
                            for row in (drifted, wrong_origin, missing)))

    def test_detached_candidate_is_pinned_and_never_observes_old_manager(self):
        sha = subprocess.run(("git", "-C", str(self.repo), "rev-parse", "HEAD"), check=True,
                             capture_output=True, text=True).stdout.strip()
        with mock.patch.object(manifest, "_manager_unit_identity",
                               side_effect=AssertionError("candidate must not query manager")):
            candidate = manifest.runtime_manifest(
                role=manifest.DETACHED_CANDIDATE, repo=self.repo, expected_commit=sha)
        self.assertTrue(candidate["valid"], candidate["identity_errors"])
        self.assertEqual(candidate["source_commit"], sha)
        self.assertIsNone(candidate["installation_identity"]["manager_configuration_sha256"])
        (self.repo / "systemd" / "altitude.service").write_text("[Service]\nExecStart=/drift\n")
        dirty = manifest.runtime_manifest(
            role=manifest.DETACHED_CANDIDATE, repo=self.repo, expected_commit=sha)
        self.assertFalse(dirty["valid"])
        self.assertIsNone(dirty["source_commit"])
        self.assertIn("systemd/altitude.service", dirty["loaded_source"]["commit_comparison"]["mismatches"])
        unpinned = manifest.runtime_manifest(role=manifest.DETACHED_CANDIDATE, repo=self.repo)
        self.assertFalse(unpinned["valid"])
        self.assertTrue(any(error.startswith("expected_commit:") for error in unpinned["identity_errors"]))

    def test_every_runtime_input_affects_content_identity_and_bundle_is_separate(self):
        baseline = self._manifest()
        baseline_source = baseline["loaded_source"]["sha256"]
        for relative in ("altitude/actions.py", "bin/alt", "scripts/restart_altitude.py",
                         "personas/l2.md", "schemas/report.json", "templates/brief.md", "hooks/guard.py"):
            path = self.repo / relative
            original = path.read_bytes()
            path.write_bytes(original + b"# changed\n")
            changed = self._manifest()
            self.assertNotEqual(changed["loaded_source"]["sha256"], baseline_source, relative)
            self.assertIsNone(changed["source_commit"], relative)
            path.write_bytes(original)
        web = self.repo / "web" / "dist" / "index.html"
        original_web = web.read_text()
        web.write_text(original_web + "changed\n")
        changed_web = self._manifest()
        self.assertEqual(changed_web["loaded_source"]["sha256"], baseline_source)
        self.assertNotEqual(changed_web["web_bundle"]["sha256"], baseline["web_bundle"]["sha256"])

    def test_git_failure_is_unknown_not_clean_and_content_identity_remains_exact(self):
        real_git = manifest._git

        def git_result(repo, *args):
            if "status" in args:
                return {"value": None, "error": "cannot read index"}
            if "refs/remotes/origin/main" in args:
                return {"value": None, "error": "unknown revision"}
            return real_git(repo, *args)

        with mock.patch.object(manifest, "_git", side_effect=git_result), \
                mock.patch.object(manifest, "_manager_unit_identity", return_value=self._manager()):
            result = manifest.runtime_manifest(role=manifest.RUNNING_INSTALL, repo=self.repo,
                                               state_home=self.root / "state",
                                               entrypoint=str(self.repo / "bin" / "alt"),
                                               startup_source=self._startup_source(),
                                               loaded_modules=self._loaded_modules())
        self.assertFalse(result["loaded_source"]["dirty"])
        self.assertEqual(result["source_commit"], result["loaded_source"]["git_head"])
        self.assertIsNotNone(result["loaded_source"]["sha256"])
        self.assertTrue(any("git_status" in error for error in result["identity_errors"]))
        self.assertIsNone(result["remote_main"]["sha"])

    def test_running_commit_is_unknown_when_executed_source_proof_fails(self):
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._manager()):
            result = manifest.runtime_manifest(
                role=manifest.RUNNING_INSTALL, repo=self.repo, state_home=self.root / "state",
                entrypoint=str(self.repo / "bin" / "alt"), startup_source=None,
                loaded_modules=self._loaded_modules())
        self.assertIsNone(result["source_commit"])
        self.assertIsNone(result["installation_identity"]["source_commit"])
        self.assertFalse(result["executed_source"]["valid"])

    def test_running_cli_ignores_valid_timestamp_stale_pyc(self):
        checkout = self.root / "source-only-checkout"
        shutil.copytree(REPO / "altitude", checkout / "altitude",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (checkout / "bin").mkdir()
        shutil.copy2(REPO / "bin" / "alt", checkout / "bin" / "alt")
        config_path = checkout / "altitude" / "config.py"
        current = config_path.read_bytes()
        prefix = b"raise RuntimeError('STALE BYTECODE EXECUTED')\n#"
        self.assertLess(len(prefix), len(current))
        stamp = 1_700_000_000
        config_path.write_bytes(prefix + b"x" * (len(current) - len(prefix)))
        os.utime(config_path, (stamp, stamp))
        py_compile.compile(str(config_path), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        config_path.write_bytes(current)
        os.utime(config_path, (stamp, stamp))
        state = self.root / "source-only-state"
        state.mkdir()
        env = os.environ | {"ALTITUDE_HOME": str(state), "ALTITUDE_TIMERS": "0"}
        result = subprocess.run(
            [sys.executable, str(checkout / "bin" / "alt"), "manifest",
             "--role", "running_install", "--repo", str(checkout)],
            cwd=checkout, env=env, capture_output=True, text=True, timeout=20)
        self.assertNotIn("STALE BYTECODE EXECUTED", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["executed_source"]["valid"], True)
        self.assertTrue(any("git_head" in error for error in payload["identity_errors"]))

    def test_required_absence_and_manager_unknown_are_explicit_identity_errors(self):
        (self.repo / "systemd" / "altitude.service").unlink()
        shutil.rmtree(self.repo / "web" / "dist")
        missing_fragment = self._manager() | {"fragment_path": str(self.root / "missing.service")}
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=missing_fragment):
            result = manifest.runtime_manifest(role=manifest.RUNNING_INSTALL, repo=self.repo,
                                               state_home=self.root / "state",
                                               entrypoint=str(self.repo / "bin" / "alt"))
        fields = {error.split(":", 1)[0] for error in result["identity_errors"]}
        self.assertTrue({"repository_unit_template", "installed_unit", "web_bundle"}.issubset(fields))
        self.assertFalse(result["service_unit"]["installed_fragment"]["present"])
        unknown = missing_fragment | {"observed": False, "sha256": None, "fragment_path": None,
                                      "error": "manager unavailable at /private/operator/path"}
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=unknown):
            unknown_result = manifest.runtime_manifest(role=manifest.RUNNING_INSTALL, repo=self.repo,
                                                       state_home=self.root / "state",
                                                       entrypoint=str(self.repo / "bin" / "alt"))
        self.assertTrue(any(error.startswith("manager_unit:")
                            for error in unknown_result["identity_errors"]))
        self.assertIsNone(unknown_result["service_unit"]["manager"]["sha256"])
        self.assertIsNone(unknown_result["service_unit"]["installed_fragment"]["present"])
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=self._manager()):
            preflight = legacy_preflight.offline_preflight(
                self._home(), repo=self.repo, manifest_role=manifest.RUNNING_INSTALL)
        self.assertTrue(preflight["cutover_blocked"])

    def test_public_projection_redacts_paths_commands_and_detailed_errors(self):
        full = self._manifest()
        full["identity_errors"].append("manager_unit: secret at /private/operator/path")
        full["service_unit"]["manager"]["error"] = "secret at /private/operator/path"
        public = manifest.public_manifest(full)
        encoded = json.dumps(public, sort_keys=True)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("/private/operator/path", encoded)
        self.assertNotIn("exec_start", encoded)
        self.assertNotIn("4242", encoded)
        self.assertTrue(public["service_unit"]["manager"]["error"])
        self.assertIn("manager_unit", public["identity_error_fields"])
        self.assertEqual(public["role"], manifest.RUNNING_INSTALL)

    def test_skip_worktree_cannot_hide_runtime_byte_drift(self):
        path = self.repo / "altitude" / "actions.py"
        subprocess.run(("git", "-C", str(self.repo), "update-index", "--skip-worktree", "altitude/actions.py"),
                       check=True)
        path.write_text("VALUE = 2\n")
        result = self._manifest()
        self.assertFalse(result["loaded_source"]["repository_dirty"])
        self.assertTrue(result["loaded_source"]["dirty"])
        self.assertIsNone(result["source_commit"])
        self.assertIn("altitude/actions.py", result["loaded_source"]["commit_comparison"]["mismatches"])
        self.assertTrue(self._preflight(self._home("hidden-drift"))["cutover_blocked"])

    def test_unprovable_head_tree_cannot_claim_commit_or_pass_preflight(self):
        unknown = {"exact": None, "mismatches": [], "error": "HEAD tree unavailable"}
        with mock.patch.object(manifest, "_commit_comparison", return_value=unknown):
            result = self._manifest()
            preflight = self._preflight(self._home("unknown-head-tree"))
        self.assertIsNone(result["source_commit"])
        self.assertIsNone(result["loaded_source"]["dirty"])
        self.assertTrue(preflight["cutover_blocked"])
        self.assertTrue(any("HEAD tree unavailable" in issue["reason"]
                            for issue in preflight["unknown_active_shapes"]))

    def test_assume_unchanged_cannot_hide_runtime_byte_drift(self):
        path = self.repo / "altitude" / "actions.py"
        subprocess.run(("git", "-C", str(self.repo), "update-index", "--assume-unchanged", "altitude/actions.py"),
                       check=True)
        path.write_text("VALUE = 3\n")
        result = self._manifest()
        self.assertFalse(result["loaded_source"]["repository_dirty"])
        self.assertTrue(result["loaded_source"]["dirty"])
        self.assertIsNone(result["source_commit"])

    def test_ignored_runtime_overlay_cannot_be_claimed_as_head(self):
        (self.repo / ".gitignore").write_text("altitude/overlay.py\n")
        subprocess.run(("git", "-C", str(self.repo), "add", ".gitignore"), check=True)
        subprocess.run(("git", "-C", str(self.repo), "commit", "-qm", "ignore fixture"), check=True)
        (self.repo / "altitude" / "overlay.py").write_text("OVERLAY = True\n")
        result = self._manifest()
        self.assertFalse(result["loaded_source"]["repository_dirty"])
        self.assertIsNone(result["source_commit"])
        self.assertIn("altitude/overlay.py", result["loaded_source"]["commit_comparison"]["mismatches"])

    def test_tracked_symlink_target_change_is_not_head_identity(self):
        link = self.repo / "personas" / "selected.md"
        link.symlink_to("l2.md")
        subprocess.run(("git", "-C", str(self.repo), "add", "personas/selected.md"), check=True)
        subprocess.run(("git", "-C", str(self.repo), "commit", "-qm", "tracked runtime symlink"), check=True)
        self.assertEqual(self._manifest()["source_commit"],
                         subprocess.run(("git", "-C", str(self.repo), "rev-parse", "HEAD"),
                                        check=True, capture_output=True, text=True).stdout.strip())
        subprocess.run(("git", "-C", str(self.repo), "update-index", "--skip-worktree",
                        "personas/selected.md"), check=True)
        link.unlink()
        link.symlink_to("reviewer.md")
        result = self._manifest()
        self.assertFalse(result["loaded_source"]["repository_dirty"])
        self.assertIsNone(result["source_commit"])
        self.assertIn("personas/selected.md", result["loaded_source"]["commit_comparison"]["mismatches"])

    def test_executable_python_symlink_is_rejected_before_import_and_never_claims_head(self):
        target = self.root / "external-config.py"
        marker = self.root / "external-config-ran"
        target.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")
        config_path = self.repo / "altitude" / "config.py"
        config_path.unlink()
        config_path.symlink_to(target)
        subprocess.run(("git", "-C", str(self.repo), "add", "altitude/config.py"), check=True)
        subprocess.run(("git", "-C", str(self.repo), "commit", "-qm", "python symlink fixture"),
                       check=True)
        result = self._manifest()
        self.assertIsNone(result["source_commit"])
        self.assertTrue(any("executable Python runtime source is a symlink" in error
                            for error in result["identity_errors"]), result["identity_errors"])

        launch_repo = self.root / "symlink-launch"
        (launch_repo / "bin").mkdir(parents=True)
        (launch_repo / "altitude").mkdir()
        shutil.copy2(REPO / "bin" / "alt", launch_repo / "bin" / "alt")
        (launch_repo / "altitude" / "__init__.py").write_text("")
        (launch_repo / "altitude" / "config.py").symlink_to(target)
        launched = subprocess.run((sys.executable, str(launch_repo / "bin" / "alt"), "serve"),
                                  capture_output=True, text=True)
        self.assertNotEqual(launched.returncode, 0)
        self.assertIn("refusing executable Python runtime symlink", launched.stderr)
        self.assertFalse(marker.exists(), "symlink target executed before source rejection")

    def test_installed_checkout_comes_from_manager_execstart_and_mismatch_blocks(self):
        other = self.root / "installed-checkout"
        shutil.copytree(self.repo, other, symlinks=True)
        manager = self._manager() | {
            "exec_start": f"{{{other / 'bin' / 'alt'} ; argv[]={other / 'bin' / 'alt'} serve ;}}",
        }
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=manager):
            result = manifest.runtime_manifest(
                role=manifest.RUNNING_INSTALL, repo=self.repo, state_home=self.root / "state",
                entrypoint=str(self.repo / "bin" / "alt"))
        self.assertEqual(result["installed_checkout"]["path"], str(other))
        self.assertTrue(any(error.startswith("manager_runtime_entry:") for error in result["identity_errors"]))
        self.assertTrue(any(error.startswith("installed_checkout:") for error in result["identity_errors"]))

    def test_manager_dropins_are_hashed_and_missing_dropin_blocks(self):
        drop_in = self.root / "10-override.conf"
        drop_in.write_text("[Service]\nEnvironment=EXACT=1\n")
        manager = self._manager() | {"drop_in_paths": [str(drop_in)]}
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=manager):
            result = manifest.runtime_manifest(
                role=manifest.RUNNING_INSTALL, repo=self.repo, state_home=self.root / "state",
                entrypoint=str(self.repo / "bin" / "alt"))
        self.assertEqual(result["service_unit"]["drop_ins"][0]["sha256"],
                         manifest._sha256(drop_in.read_bytes()))
        missing = manager | {"drop_in_paths": [str(self.root / "missing.conf")]}
        with mock.patch.object(manifest, "_manager_unit_identity", return_value=missing):
            blocked = manifest.runtime_manifest(
                role=manifest.RUNNING_INSTALL, repo=self.repo, state_home=self.root / "state",
                entrypoint=str(self.repo / "bin" / "alt"))
        self.assertTrue(any(error.startswith("installed_drop_in:") for error in blocked["identity_errors"]))

    def test_manager_query_failure_is_explicit_unknown(self):
        failure = subprocess.CompletedProcess([], 1, "", "Failed to connect to bus")
        with mock.patch.object(manifest.subprocess, "run", return_value=failure) as run:
            result = manifest._manager_unit_identity(observe=True)
        self.assertFalse(result["observed"])
        self.assertIsNone(result["sha256"])
        self.assertIsNone(result["fragment_path"])
        self.assertIn("Failed to connect to bus", result["error"])
        self.assertEqual(run.call_args.args[0][:4],
                         ["systemctl", "--user", "show", "altitude.service"])

    def test_partial_manager_observation_is_unknown(self):
        output = "\n".join((
            f"FragmentPath={self.installed_unit}",
            f"ExecStart={{{self.repo / 'bin' / 'alt'} ; argv[]={self.repo / 'bin' / 'alt'} serve ;}}",
            "LoadState=loaded", "SubState=running", "UnitFileState=enabled", "NeedDaemonReload=no",
        ))
        with mock.patch.object(manifest.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, output, "")):
            result = manifest._manager_unit_identity(observe=True)
        self.assertFalse(result["observed"])
        self.assertIsNone(result["sha256"])
        self.assertIn("ActiveState", result["error"])
        self.assertIn("DropInPaths", result["error"])
        self.assertIn("MainPID", result["error"])
        self.assertIn("ControlGroup", result["error"])

    def test_git_and_copied_state_remain_byte_stable_and_fsmonitor_is_disabled(self):
        sentinel = self.root / "fsmonitor-invoked"
        fsmonitor = self.repo / ".git" / "phase0b-fsmonitor.sh"
        fsmonitor.write_text(f"#!/bin/sh\nprintf invoked > {sentinel}\n")
        fsmonitor.chmod(0o755)
        subprocess.run(("git", "-C", str(self.repo), "config", "core.fsmonitor", str(fsmonitor)), check=True)
        home = self._home()
        before_repo, before_home = _snapshot(self.repo), _snapshot(home)
        first = self._preflight(home)
        second = self._preflight(home)
        self.assertEqual(first, second)
        self.assertFalse(first["cutover_blocked"], first["unknown_active_shapes"])
        self.assertEqual(before_repo, _snapshot(self.repo))
        self.assertEqual(before_home, _snapshot(home))
        self.assertFalse(sentinel.exists())

    def test_git_observations_override_hooks_fsmonitor_and_optional_writes(self):
        completed = subprocess.CompletedProcess([], 0, "clean\n", "")
        with mock.patch.object(manifest.subprocess, "run", return_value=completed) as run:
            result = manifest._git(self.repo, "status", "--porcelain")
        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(result["value"], "clean")
        self.assertIn("core.hooksPath=/dev/null", command)
        self.assertIn("core.fsmonitor=false", command)
        self.assertIn("core.untrackedCache=false", command)
        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")

    def test_active_namespaces_and_future_shapes_fail_closed_archive_only_reports(self):
        cases = []
        monitor_home = self._home("monitor-unknown")
        _write_json(monitor_home / "monitor" / "surprise.json", {})
        cases.append((monitor_home, "unrecognized monitor file"))
        task_home = self._home("task-unknown")
        task = self._task(task_home, "tasks", "queued", "queued")
        (task / "future.bin").write_bytes(b"future")
        cases.append((task_home, "unrecognized task file"))
        project_home = self._home("project-unknown")
        (project_home / "demo" / "future.bin").write_bytes(b"future")
        cases.append((project_home, "unrecognized project file"))
        future_home = self._home("future-shape")
        _write_json(future_home / "monitor" / "quota-codex.json", {"schema_version": "future"})
        cases.append((future_home, "unsupported monitor record schema_version"))
        future_task_home = self._home("future-task-shape")
        self._task(future_task_home, "tasks", "future", "queued", schema_version="future")
        cases.append((future_task_home, "unsupported task_status schema_version"))
        for home, reason in cases:
            result = self._preflight(home)
            self.assertTrue(result["cutover_blocked"], home.name)
            self.assertTrue(any(reason in issue["reason"] for issue in result["unknown_active_shapes"]),
                            result["unknown_active_shapes"])
        archive_home = self._home("archive-unknown")
        archived_task = self._task(archive_home, "archive", "done", "done")
        (archived_task / "future.bin").write_bytes(b"future")
        archived = self._preflight(archive_home)
        self.assertFalse(archived["cutover_blocked"], archived["unknown_active_shapes"])
        self.assertTrue(any("unrecognized task file" in issue["reason"]
                            for issue in archived["unknown_archived_shapes"]))

    def test_unknown_active_authority_variants_and_extra_fields_fail_closed(self):
        helper_home = self._home("unknown-helper")
        task = self._task(helper_home, "tasks", "queued", "queued")
        _write_json(task / "l1" / "review.json", {
            "name": "review", "role": "reviewer", "engine": "claude", "state": "future",
            "pid": 123, "worktree": "/tmp/review", "done": None,
        })
        l3_home = self._home("unknown-l3")
        _write_json(l3_home / "demo" / "l3.json", {"sessions": {}, "future_owner": "x"})
        action_home = self._home("unknown-action")
        _write_json(action_home / "demo" / "l3-actions" / ("a" * 64 + ".json"), {
            "id": "a" * 64, "at": "now", "status": "future", "action": {"type": "new_task"},
        })
        for home in (helper_home, l3_home, action_home):
            result = self._preflight(home)
            self.assertTrue(result["cutover_blocked"], home.name)
            self.assertTrue(result["unknown_active_shapes"], home.name)

    def test_nested_task_and_recovery_authority_shapes_fail_closed(self):
        cases = []
        completion = self._home("nested-completion")
        self._task(completion, "tasks", "queued", "queued", completion_requested={
            "at": "now", "digest": "done", "dispatch_id": "d", "session_id": "s",
            "agent_id": "a", "authority": "future",
        })
        cases.append((completion, "unknown completion request fields"))
        routing = self._home("nested-routing")
        self._task(routing, "tasks", "queued", "queued", routing={
            "engine": "codex", "why": "test", "quota": {
                "claude": {"weekly_used": None, "short_used": None, "weekly_resets": None,
                           "short_resets": None, "observed_at": None, "raw": {"known": False}},
                "codex": {"weekly_used": None, "short_used": None, "weekly_resets": None,
                          "short_resets": None, "observed_at": None, "raw": {"known": False}},
            }, "future_authority": True,
        })
        cases.append((routing, "unknown routing decision fields"))
        pending = self._home("nested-pending")
        self._task(pending, "tasks", "queued", "queued", pending_action={
            "action": {}, "identity": {}, "claimed": "now", "message_posted": False,
            "future_authority": True,
        })
        cases.append((pending, "unknown pending action fields"))
        repair = self._home("nested-repair")
        _write_json(repair / "monitor" / "recovery-hold.json", {
            "active": True, "since": "now", "episode": "e", "faults": [], "updated": "now",
            "repair": {"project": "demo", "slug": "fix", "claimed": "now", "by": "l3",
                       "future_authority": True},
            "l3_attention": None,
        })
        cases.append((repair, "unknown recovery repair fields"))
        attention = self._home("nested-attention")
        _write_json(attention / "monitor" / "recovery-hold.json", {
            "active": True, "since": "now", "episode": "e", "faults": [], "updated": "now",
            "repair": None,
            "l3_attention": {"episode": "e", "project": "demo", "requested": "now",
                             "updated": "now", "revision": 1, "handled_revision": 0,
                             "attempts": 0, "next_attempt": None, "faults": [],
                             "future_authority": True},
        })
        cases.append((attention, "unknown recovery L3 attention fields"))
        for home, reason in cases:
            result = self._preflight(home)
            self.assertTrue(result["cutover_blocked"], home.name)
            self.assertTrue(any(reason in issue["reason"] for issue in result["unknown_active_shapes"]),
                            result["unknown_active_shapes"])

    def test_both_provider_worker_records_are_validated_as_physical_evidence(self):
        codex_home = self._home("codex-physical")
        codex_task = self._task(
            codex_home, "tasks", "codex", "running", attempt=1, dispatch_id="codex-1",
            session_id="thread", agent_id="worker", l2_engine="codex",
            worktree="/tmp/codex-worktree", branch="worktree-codex")
        codex_record = {
            "id": "worker", "name": "demo/codex-1", "pid": 123, "pid_start": 456,
            "unit": "altitude-l2-worker.scope", "started_at": "now", "session_id": "thread",
            "cwd": "/tmp/codex-worktree", "resume": False, "stopped": None,
        }
        _write_json(codex_task / "l2-engine" / "worker.json", codex_record)
        valid_codex = self._preflight(codex_home)["unknown_active_shapes"]
        self.assertTrue(any("active v1 task" in issue["reason"] for issue in valid_codex))
        self.assertFalse(any("unknown Codex worker fields" in issue["reason"]
                             for issue in valid_codex))
        _write_json(codex_task / "l2-engine" / "worker.json",
                    {**codex_record, "future_authority": True})
        self.assertTrue(any("unknown Codex worker fields" in issue["reason"]
                            for issue in self._preflight(codex_home)["unknown_active_shapes"]))

        claude_home = self._home("claude-physical")
        self._task(claude_home, "tasks", "claude", "running", attempt=1,
                   dispatch_id="claude-1", session_id="session", agent_id="agent",
                   l2_engine="claude", worktree="/tmp/claude-worktree", branch="worktree-claude")
        provider_home = self.root / "provider-home"
        claude_record = {
            "id": "agent", "name": "demo/claude-1", "sessionId": "session",
            "state": "working", "status": "busy", "pid": 789, "startedAt": "now",
            "worktreePath": "/tmp/claude-worktree", "worktreeBranch": "worktree-claude",
        }
        record_path = provider_home / ".claude" / "jobs" / "agent" / "state.json"
        _write_json(record_path, claude_record)
        valid_claude = self._preflight(
            claude_home, provider_home=provider_home)["unknown_active_shapes"]
        self.assertTrue(any("active v1 task" in issue["reason"] for issue in valid_claude))
        self.assertFalse(any("unknown Claude worker fields" in issue["reason"]
                             for issue in valid_claude))
        _write_json(record_path, {**claude_record, "future_authority": True})
        self.assertTrue(any("unknown Claude worker fields" in issue["reason"]
                            for issue in self._preflight(
                                claude_home, provider_home=provider_home)["unknown_active_shapes"]))

    def test_valid_project_hold_and_orphan_live_claude_job_block_cutover(self):
        held_home = self._home("active-project-hold")
        _write_json(held_home / "demo" / "hold.json", {"at": "now", "reason": "held"})
        held = self._preflight(held_home)
        self.assertTrue(held["cutover_blocked"])
        self.assertTrue(any("active v1 project hold" in issue["reason"]
                            for issue in held["unknown_active_shapes"]), held["unknown_active_shapes"])

        worker_home = self._home("orphan-provider-worker")
        provider_home = self.root / "orphan-provider-home"
        _write_json(provider_home / ".claude" / "jobs" / "orphan" / "state.json", {
            "id": "orphan", "name": "demo/orphan", "sessionId": "session",
            "state": "working", "status": "busy", "pid": 789, "startedAt": "now",
            "worktreePath": "/tmp/orphan", "worktreeBranch": "worktree-orphan",
        })
        active = self._preflight(worker_home, provider_home=provider_home)
        self.assertTrue(active["cutover_blocked"])
        self.assertTrue(any("active v1 Claude provider job" in issue["reason"]
                            for issue in active["unknown_active_shapes"]),
                        active["unknown_active_shapes"])
        stopped_record = json.loads(
            (provider_home / ".claude" / "jobs" / "orphan" / "state.json").read_text())
        _write_json(provider_home / ".claude" / "jobs" / "orphan" / "state.json",
                    {**stopped_record, "state": "done", "status": "exited"})
        settled = self._preflight(worker_home, provider_home=provider_home)
        self.assertFalse(settled["cutover_blocked"], settled["unknown_active_shapes"])

    def test_unregistered_project_is_archive_only_only_when_exclusively_archive(self):
        active_markers = {
            "state": ("STATE.md", ""), "l3": ("l3.json", {}), "hold": ("hold.json", {}),
            "tasks": ("tasks/queued/status.json", {}), "chat": ("chat.jsonl", ""),
            "inbox": ("inbox.jsonl", ""), "events": ("events.log", ""),
            "incidents": ("incidents.jsonl", ""), "actions": ("l3-actions/action.json", {}),
        }
        for label, (relative, value) in active_markers.items():
            home = self._home(f"orphan-{label}")
            _write_json(home / "projects.json", {})
            path = home / "demo" / relative
            if isinstance(value, dict):
                _write_json(path, value)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)
            result = self._preflight(home)
            self.assertTrue(result["cutover_blocked"], label)
            self.assertTrue(any("absent from projects.json" in issue["reason"]
                                or "unrecognized state-root directory" in issue["reason"]
                                for issue in result["unknown_active_shapes"]), label)

        mixed_home = self._home("orphan-archive-with-unknown-root")
        _write_json(mixed_home / "projects.json", {})
        self._task(mixed_home, "archive", "done", "done")
        (mixed_home / "demo" / "mystery.bin").write_bytes(b"unknown")
        mixed = self._preflight(mixed_home)
        self.assertTrue(mixed["cutover_blocked"])
        self.assertTrue(any("absent from projects.json" in issue["reason"]
                            for issue in mixed["unknown_active_shapes"]))

        archived_home = self._home("orphan-archive-only")
        _write_json(archived_home / "projects.json", {})
        self._task(archived_home, "archive", "done", "done")
        archived = self._preflight(archived_home)
        self.assertFalse(archived["cutover_blocked"], archived["unknown_active_shapes"])
        self.assertTrue(any("absent from projects.json" in issue["reason"]
                            for issue in archived["unknown_archived_shapes"]))

    def test_monitor_reference_makes_an_archive_only_orphan_active(self):
        home = self._home("orphan-monitor-reference")
        _write_json(home / "projects.json", {})
        self._task(home, "archive", "done", "done")
        _write_json(home / "monitor" / "faults.json", {
            "ownership": {"project": "demo", "count": 1, "incident": "I-001"},
        })
        result = self._preflight(home)
        self.assertTrue(result["cutover_blocked"])
        self.assertTrue(any("monitor state references unregistered project 'demo'" in issue["reason"]
                            for issue in result["unknown_active_shapes"]))

    def test_offline_manager_override_is_explicit_and_still_blocks_cutover(self):
        result = legacy_preflight.offline_preflight(
            self._home(), repo=self.repo, manifest_role=manifest.RUNNING_INSTALL,
            observe_manager=False)
        self.assertTrue(result["cutover_blocked"])
        self.assertFalse(result["manifest"]["service_unit"]["manager"]["observed"])
        self.assertTrue(any("manager observation disabled" in issue["reason"]
                            for issue in result["unknown_active_shapes"]))

    def test_artifact_catalog_matches_proposal_families_and_separates_evidence(self):
        rows = legacy_preflight._artifact_inventory()
        expected = {
            "projects", "project-publication-resume-locks", "recovery-hold", "recovery-clearances",
            "restart-pending", "global-incidents", "monitor-faults",
            "quota-statusline-usage-observations", "hook-fault-drain", "edit-counts",
            "live-worker-cache", "project-state", "project-events", "project-inbox", "project-hold",
            "project-incidents-markdown", "claude-settings", "l3-sessions", "l3-chat", "l3-actions",
            "issue-drafts", "task-status", "request", "brief", "conversation", "task-events",
            "issue-snapshot", "report", "task-digest", "progress", "task-claude-settings",
            "helper-bundle", "codex-worker-bundle", "transcript-bundle", "worktree-ref-provenance",
            "report-md-compatibility", "global-digest-audio", "runtime-hooks-directory", "service-log",
            "tls-material", "web-bundle", "service-manager", "task-namespaces",
            "l3-codex-scratch", "claude-job-state", "claude-session-records",
        }
        self.assertEqual({row["name"] for row in rows}, expected)
        self.assertEqual(len(rows), 46)
        self.assertTrue(all(row["active_consumer_count"] == len(row["active_consumers"])
                            for row in rows))
        self.assertTrue(all(row["evidence_kind"].startswith("mechanically observed") for row in rows))
        by_name = {row["name"]: row for row in rows}
        self.assertGreater(by_name["web-bundle"]["active_consumer_count"], 0)
        self.assertGreater(by_name["tls-material"]["active_consumer_count"], 0)
        restart = next(row for row in rows if row["name"] == "restart-pending")
        self.assertEqual(restart["declared_consumers"], ["dispatch", "server"])
        self.assertEqual(restart["declared_writers"], ["dispatch", "server"])
        inventory = legacy_preflight._source_inventory(self.repo)
        self.assertIn("mechanically_observed_writer_call_sites", inventory)
        self.assertIn("mechanically_observed_timer_paths", inventory)
        self.assertFalse(inventory["unclassified_files"])
        self.assertFalse(inventory["missing_roster_files"])
        self.assertEqual({row["path"] for row in inventory["permanent_runnable"]["files"]},
                         set(legacy_preflight.PERMANENT_BACKEND_ROSTER
                             + legacy_preflight.WEB_SOURCE_ROSTER
                             + legacy_preflight.WEB_BUILD_ROSTER))
        self.assertEqual(set(inventory["dependencies"]),
                         set(legacy_preflight.PERMANENT_BACKEND_ROSTER
                             + legacy_preflight.WEB_SOURCE_ROSTER
                             + legacy_preflight.WEB_BUILD_ROSTER
                             + legacy_preflight.TEMPORARY_ROSTER
                             + legacy_preflight.PERSONA_SCHEMA_TEMPLATE_ROSTER
                             + legacy_preflight.SUPPORT_ROSTER))
        calls = {row["call"] for row in inventory["mechanically_observed_writer_call_sites"]}
        self.assertTrue({"path.chmod", "tempfile.mkstemp", "os.fdopen",
                         "tempfile.NamedTemporaryFile", "os.open"}.issubset(calls), calls)

    def test_cli_manifest_does_not_create_configured_state_home(self):
        configured_home = self.root / "configured-home"
        env = {**os.environ, "ALTITUDE_HOME": str(configured_home)}
        sha = subprocess.run(("git", "-C", str(self.repo), "rev-parse", "HEAD"), check=True,
                             capture_output=True, text=True).stdout.strip()
        result = subprocess.run([sys.executable, str(REPO / "bin" / "alt"), "manifest",
                                 "--role", "detached_candidate", "--repo", str(self.repo),
                                 "--candidate-sha", sha],
                                cwd=REPO, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(configured_home.exists())
        payload = json.loads(result.stdout)
        self.assertIn("checkout", payload["loaded_source"])
        self.assertIn("path", payload["service_unit"]["repository_template"])

    def test_cli_preflight_refusal_does_not_create_either_home(self):
        configured_home = self.root / "configured-home"
        missing_copy = self.root / "missing-copy"
        env = {**os.environ, "ALTITUDE_HOME": str(configured_home)}
        result = subprocess.run(
            [sys.executable, str(REPO / "bin" / "alt"), "preflight", "--home", str(missing_copy),
             "--role", "detached_candidate", "--repo", str(self.repo),
             "--candidate-sha", subprocess.run(
                 ("git", "-C", str(self.repo), "rev-parse", "HEAD"), check=True,
                 capture_output=True, text=True).stdout.strip()],
            cwd=REPO, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(configured_home.exists())
        self.assertFalse(missing_copy.exists())
        self.assertTrue(json.loads(result.stdout)["cutover_blocked"])


if __name__ == "__main__":
    unittest.main()
