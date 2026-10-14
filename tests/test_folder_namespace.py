"""Fictional folder namespaces: alias parity, pinned races and operation-specific permission limits."""
import errno
import os
import stat
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

from tests.test_folders import FolderCase
from altitude import config, platform, server


class FolderNamespace(FolderCase):
    def test_folder_normalization_keeps_posix_parent_at_filesystem_root(self):
        path = Path("/.." + str(self.home / "Projects"))
        self.assertEqual(path.resolve(), self.home / "Projects")
        self.assertEqual(server.folders(str(path))["path"], str(self.home / "Projects"))
        config.validate_projects_folder(str(path))

    def chain(self, parent, count, target, prefix="link"):
        for index in reversed(range(count)):
            (parent / f"{prefix}{index}").symlink_to(target if index == count - 1 else f"{prefix}{index + 1}")
        return parent / f"{prefix}0"

    def test_browser_alias_can_leave_home_and_return_but_private_intermediate_is_refused(self):
        outside = self.tmp / "outside"
        (outside / "return").symlink_to(self.home / "Projects")
        (self.home / "via-outside").symlink_to(outside / "return")
        self.assertEqual(server.folders(str(self.home / "via-outside"))["path"], str(self.home / "Projects"))
        with mock.patch.object(platform, "private_places", return_value=(str(outside),)):
            with self.no_private_lookups(outside), self.assertRaises(server.FolderError) as caught:
                server.folders(str(self.home / "via-outside"))
            self.assertEqual(caught.exception.status, 403)

    @contextmanager
    def no_private_lookups(self, private):
        """Every actual candidate I/O is relative to a held descriptor; unauthorized metadata is an error."""
        info, parent_info = private.stat(), private.parent.stat()
        private_identity = info.st_dev, info.st_ino
        parent_identity = parent_info.st_dev, parent_info.st_ino
        with ExitStack() as stack:
            for name in ("stat", "readlink", "open"):
                original = getattr(os, name)
                def guarded(path, *args, _original=original, **kwargs):
                    if isinstance(path, (str, Path)):
                        parent = kwargs.get("dir_fd")
                        if parent is not None:
                            observed = os.fstat(parent)
                            identity = observed.st_dev, observed.st_ino
                            self.assertNotEqual(identity, private_identity)
                            if str(path) == private.name and identity == parent_identity:
                                self.fail("private candidate lookup")
                        elif str(path) == str(private) or str(path).startswith(str(private) + "/"):
                            self.fail("private absolute lookup")
                    return _original(path, *args, **kwargs)
                stack.enter_context(mock.patch.object(os, name, side_effect=guarded))
            yield

    def test_accepted_ancestor_replacement_keeps_original_namespace_for_selection_and_listing(self):
        private = self.tmp / "private"
        private.mkdir()
        (private / "child").mkdir()
        accepted = self.home / "safe"
        accepted.mkdir()
        (accepted / "child").mkdir()
        original = os.open
        moved = self.home / "old-safe"
        changed = []
        def opening(name, flags, *args, **kwargs):
            fd = original(name, flags, *args, **kwargs)
            if name == "safe" and not changed:
                accepted.rename(moved)
                accepted.symlink_to(private)
                changed.append(True)
            return fd
        info = private.stat()
        self.private_identity = info.st_dev, info.st_ino
        with mock.patch.object(platform, "private_places", return_value=(str(private),)), \
                self.no_private_lookups(private), mock.patch.object(os, "open", side_effect=opening):
            view = server.folders(str(accepted / "child"))
        self.assertEqual((view["path"], view["readable"]), (str(accepted / "child"), True))
        self.assertTrue(changed)
        accepted.unlink()
        moved.rename(accepted)

    def test_listed_parent_replacement_never_redirects_child_or_git_metadata(self):
        root = self.home / "code"
        private = self.tmp / "private"
        (private / "atlas" / ".git").mkdir(parents=True)
        original = os.listdir
        info = private.stat()
        self.private_identity = info.st_dev, info.st_ino
        moved = self.home / "old-code"
        changed = []
        root_inode = root.stat().st_ino
        def listed(fd):
            names = original(fd)
            if not changed and os.fstat(fd).st_ino == root_inode:
                root.rename(moved)
                root.symlink_to(private)
                changed.append(True)
            return names
        with mock.patch.object(platform, "private_places", return_value=(str(private),)), \
                self.no_private_lookups(private), mock.patch.object(os, "listdir", side_effect=listed):
            view = server.folders(str(root))
        self.assertEqual([(r["name"], r["git"]) for r in view["folders"]], [("atlas", True), ("notes", False)])
        root.unlink()
        moved.rename(root)

    def test_replacement_before_directory_open_is_refused_with_no_private_follow(self):
        private = self.tmp / "private"
        private.mkdir()
        safe = self.home / "safe"
        safe.mkdir()
        info = private.stat()
        self.private_identity = info.st_dev, info.st_ino
        original = os.open
        moved = self.home / "old-safe"
        changed = []
        def opening(name, flags, *args, **kwargs):
            if name == "safe" and not changed:
                safe.rename(moved)
                safe.symlink_to(private)
                changed.append(True)
            return original(name, flags, *args, **kwargs)
        with mock.patch.object(platform, "private_places", return_value=(str(private),)), \
                self.no_private_lookups(private), mock.patch.object(os, "open", side_effect=opening):
            with self.assertRaises(ValueError):
                config.validate_projects_folder(str(safe))
        safe.unlink()
        moved.rename(safe)

    def test_caller_link_boundaries_and_long_component_normalization(self):
        places = () if not hasattr(os, "O_SEARCH") else (str(self.tmp / "fictional-private"),)
        limit = platform.directory_link_limit()
        with mock.patch.object(platform, "private_places", return_value=places):
            for count in (limit, limit + 1):
                alias = self.chain(self.home, count, self.home / "Projects", prefix=f"n{count}-")
                if count == limit:
                    config.validate_projects_folder(str(alias))
                else:
                    with self.assertRaises(ValueError):
                        config.validate_projects_folder(str(alias))
                # Mac raw private operation refuses above32; Linux normalizes before final type.
                if places and count > limit:
                    with self.assertRaises(server.FolderError):
                        server.folders(str(alias))
                else:
                    self.assertEqual(server.folders(str(alias))["path"], str(self.home / "Projects"))
            if not places:
                alias = self.chain(self.home, 64, self.home / "Projects", prefix="long-")
                self.assertEqual(server.folders(str(alias))["path"], str(self.home / "Projects"))
                self.assertEqual(config.managed_addresses({"long": {"path": str(alias)}}), {str(self.home / "Projects"): "long"})

    def test_reused_alias_charges_logical_cost_and_keeps_bounded_work(self):
        (self.home / "repeat").symlink_to("Projects")
        limit = platform.directory_link_limit()
        for count in (limit, limit + 1):
            path = self.home / Path(*(["repeat", ".."] * (count - 1)), "repeat")
            with mock.patch.object(platform, "private_places", return_value=()):
                if count == limit:
                    config.validate_projects_folder(str(path))
                else:
                    with self.assertRaises(ValueError):
                        config.validate_projects_folder(str(path))
        original = os.readlink
        with mock.patch.object(os, "readlink", wraps=original) as reads, \
                mock.patch.object(platform, "private_places", return_value=()):
            with platform.inspected_path(path, limit=None) as inspected:
                self.assertEqual(inspected.links, limit + 1)
            self.assertLessEqual(reads.call_count, 2)

    def test_marker_budget_includes_parent_aliases_and_never_reads_marker_contents(self):
        limit = platform.directory_link_limit()
        project = self.home / "Projects" / "repo"
        project.mkdir()
        marker = project / "marker"
        marker.write_text("fictional metadata")
        marker.chmod(0)
        self.addCleanup(marker.chmod, 0o600)
        (project / ".git").symlink_to("marker")
        with mock.patch.object(platform, "private_places", return_value=()):
            for count in (limit - 1, limit):
                alias = self.chain(self.home, count, project, prefix=f"marker{count}-")
                with platform.inspected_path(alias, limit=limit, open_leaf=False) as directory:
                    self.assertEqual(platform.git_marker(directory), count < limit)

    def test_browser_children_reset_canonical_parent_while_discovery_retains_raw_root_cost(self):
        root = self.home / "Projects"
        project = root / "actual"
        project.mkdir()
        (project / "marker").mkdir()
        (project / ".git").symlink_to("marker")
        (root / "shortcut").symlink_to("actual")
        count = platform.directory_link_limit() - 1
        alias = self.chain(self.home, count, root, prefix="root-")
        self.patch(config, "PROJECT_ROOTS", [alias])
        with mock.patch.object(platform, "private_places", return_value=()):
            browsed = {row["name"]: row for row in server.folders(str(alias))["folders"]}
            discovered = {row["folder"]: row for row in config.discover_projects()}
        self.assertTrue(browsed["shortcut"]["git"])
        self.assertTrue(discovered["actual"]["git"])
        self.assertFalse(discovered["shortcut"]["git"])
        self.assertEqual(discovered["shortcut"]["path"], str(alias / "shortcut"))

    def test_link_replacement_during_readlink_refuses_before_private_lookup(self):
        private = self.tmp / "private"
        private.mkdir()
        alias = self.home / "shortcut"
        alias.symlink_to(self.home / "Projects")
        original = os.readlink
        changed = []
        def reading(name, *args, **kwargs):
            target = original(name, *args, **kwargs)
            if name == "shortcut" and not changed:
                alias.unlink()
                alias.symlink_to(private)
                changed.append(True)
            return target
        with mock.patch.object(platform, "private_places", return_value=(str(private),)), \
                self.no_private_lookups(private), mock.patch.object(os, "readlink", side_effect=reading):
            with self.assertRaises(ValueError):
                config.validate_projects_folder(str(alias))
        self.assertTrue(changed)

    def test_discovery_replacement_uses_held_root_and_managed_address(self):
        root = self.home / "code"
        private = self.tmp / "private"
        (private / "atlas").mkdir(parents=True)
        self.patch(config, "PROJECT_ROOTS", [root])
        self.register("managed-atlas", path=root / "atlas")
        root_inode = root.stat().st_ino
        original = os.listdir
        moved = self.home / "old-code"
        changed = []
        def listed(fd):
            names = original(fd)
            if not changed and os.fstat(fd).st_ino == root_inode:
                root.rename(moved)
                root.symlink_to(private)
                changed.append(True)
            return names
        with mock.patch.object(platform, "private_places", return_value=(str(private),)), \
                self.no_private_lookups(private), mock.patch.object(os, "listdir", side_effect=listed):
            rows = {r["name"]: r for r in config.discover_projects()}
        self.assertTrue(rows["managed-atlas"]["git"])
        self.assertEqual(rows["managed-atlas"]["path"], str(root / "atlas"))
        self.assertTrue(changed)
        root.unlink()
        moved.rename(root)

    @unittest.skipIf(os.geteuid() == 0, "nonroot final-leaf permission parity")
    def test_registration_final_type_needs_no_execute_read_or_listing(self):
        ancestor = self.home / "search-only"
        leaf = ancestor / "type-only"
        leaf.mkdir(parents=True)
        ancestor.chmod(0o100)
        leaf.chmod(0)
        self.addCleanup(ancestor.chmod, 0o700)
        self.addCleanup(leaf.chmod, 0o700)
        original_registry = config.load_projects()
        self.addCleanup(config.save_projects, original_registry)
        with config.add_project("type-only", path=leaf):
            pass
        self.assertEqual(config.load_projects()["type-only"]["path"], str(leaf))
        with self.assertRaisesRegex(ValueError, "not readable"):
            config.validate_projects_folder(str(leaf))

    @unittest.skipIf(os.geteuid() == 0, "nonroot ancestor permission parity")
    def test_selection_distinguishes_uninspectable_type_from_unreadable_known_directory(self):
        parent = self.home / "unsearchable"
        leaf = parent / "folder"
        leaf.mkdir(parents=True)
        parent.chmod(0)
        self.addCleanup(parent.chmod, 0o700)
        with self.assertRaisesRegex(ValueError, "not a directory"):
            config.validate_projects_folder(str(leaf))

    def test_private_marker_is_unavailable_without_hiding_safe_child(self):
        marker = self.home / "code" / "atlas" / ".git"
        marker.rmdir()
        private = self.tmp / "private"
        private.mkdir()
        marker.symlink_to(private)
        info = private.stat()
        self.private_identity = info.st_dev, info.st_ino
        with mock.patch.object(platform, "private_places", return_value=(str(private),)), self.no_private_lookups(private):
            rows = {r["name"]: r for r in server.folders(str(self.home / "code"))["folders"]}
        self.assertFalse(rows["atlas"]["git"])

    def test_container_containment_uses_inspected_volume_and_destination(self):
        self.patch(platform, "containerized", return_value=True)
        self.patch(platform, "CONTAINER_PROJECTS", self.home / "Projects")
        self.patch(Path, "resolve", side_effect=AssertionError("unchecked resolve"))
        config.validate_projects_folder(str(self.home / "Projects"))
        with self.assertRaisesRegex(ValueError, "inside the container"):
            config.validate_projects_folder(str(self.tmp / "outside"))
        with self.assertRaisesRegex(ValueError, "inside the container"):
            config.validate_projects_folder(str(self.tmp / "missing-outside" / "Projects"))
        with self.assertRaisesRegex(ValueError, "inside the container"):
            with config.add_project("volume-root", path=self.home / "Projects"):
                pass

    def test_selection_keeps_kernel_directory_semantics_after_safe_normalization(self):
        for first in (self.home / "missing", self.home / "regular"):
            if first.name == "regular":
                first.write_text("fictional file")
            path = first / ".." / "Projects"
            # Browser component normalization permits these paths; raw directory operations do not.
            self.assertEqual(server.folders(str(path))["path"], str(self.home / "Projects"))
            with self.assertRaisesRegex(ValueError, "not a directory"):
                config.validate_projects_folder(str(path))
            with self.assertRaisesRegex(ValueError, "not a directory"):
                with config.add_project("unsearchable", path=path):
                    pass

    def test_missing_container_root_remains_a_configured_unavailable_root(self):
        volume = self.home / "not-created"
        self.patch(platform, "containerized", return_value=True)
        self.patch(platform, "CONTAINER_PROJECTS", volume)
        self.assertEqual(config.project_roots(), [volume])
        with self.assertRaisesRegex(ValueError, "not a directory"):
            config.validate_projects_folder(str(volume))

    def test_container_volume_replacement_cannot_relabel_an_old_namespace(self):
        volume = self.home / "Projects"
        child = volume / "repository"
        child.mkdir()
        self.patch(platform, "containerized", return_value=True)
        self.patch(platform, "CONTAINER_PROJECTS", volume)
        moved = self.home / "old-volume"
        with platform.inspected_path(child, limit=None, open_leaf=False) as inspected:
            volume.rename(moved)
            volume.mkdir()
            with self.assertRaisesRegex(ValueError, "volume changed"):
                platform.require_container_project(inspected)
        volume.rmdir()
        moved.rename(volume)

    def test_descriptor_cleanup_includes_partial_dup_failure(self):
        original_dup, original_close = os.dup, os.close
        duplicates, closed = [], []
        with platform.inspected_path(self.home, limit=None) as parent:
            def duplicate(fd):
                if len(duplicates) == 2:
                    raise OSError(errno.EMFILE, "fictional descriptor exhaustion")
                result = original_dup(fd)
                duplicates.append(result)
                return result
            def closing(fd):
                closed.append(fd)
                original_close(fd)
            with mock.patch.object(os, "dup", side_effect=duplicate), mock.patch.object(os, "close", side_effect=closing):
                with self.assertRaises(OSError):
                    with platform.inspected_child(parent, "Projects", inherited_links=0):
                        pass
            self.assertCountEqual(duplicates, closed)
