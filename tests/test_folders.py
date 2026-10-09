"""The folder browser behind "A folder elsewhere" and the projects folder machine setting (issue #479).

Browsing lists one folder the operator opened: its visible subfolders inside the home folder, never files or
contents. The projects folder is the one folder First run lists the immediate subfolders of.
"""
import json
import os
import unittest
from pathlib import Path

from tests.support import AltitudeCase
from altitude import config, dispatch, server


class FolderCase(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / "code" / "atlas" / ".git").mkdir(parents=True)
        (self.home / "code" / "notes").mkdir()
        (self.home / "code" / "README.md").write_text("private words")
        (self.home / "Projects").mkdir()
        (self.home / ".secrets" / "keys").mkdir(parents=True)
        (self.tmp / "outside").mkdir()
        self.patch(config, "HOME", self.home)
        settings = config.ROOT / "settings.json"
        saved = {path: path.read_text() if path.exists() else None
                 for path in (settings, config.ROOT / "projects_folder-request.json")}
        self.addCleanup(lambda: [path.write_text(text) if text is not None else path.unlink(missing_ok=True)
                                 for path, text in saved.items()])
        (config.ROOT / "projects_folder-request.json").unlink(missing_ok=True)


class TestFolders(FolderCase):
    def test_home_is_the_start_and_lists_visible_subfolders_only(self):
        view = server.folders(None)
        self.assertEqual((view["path"], view["parts"], view["readable"]), (str(self.home.resolve()), [], True))
        self.assertEqual([row["name"] for row in view["folders"]], ["code", "Projects"])

    def test_a_listing_names_folders_and_tags_without_files_or_contents(self):
        self.register("atlas-project", path=self.home / "code" / "atlas")
        view = server.folders(str(self.home / "code"))
        self.assertEqual(view["parts"], ["code"])
        self.assertEqual(view["folders"], [
            {"name": "atlas", "path": str(self.home / "code" / "atlas"), "project": "atlas-project", "git": True},
            {"name": "notes", "path": str(self.home / "code" / "notes"), "project": None, "git": False}])
        self.assertNotIn("README.md", json.dumps(view))
        self.assertNotIn("private words", json.dumps(view))

    def test_browsing_stays_inside_home_and_out_of_hidden_folders(self):
        for path in (str(self.tmp / "outside"), "/", str(self.home / ".secrets"), str(self.home / ".secrets" / "keys"),
                     str(self.home / "code" / ".." / "..")):
            with self.subTest(path=path), self.assertRaises(server.FolderError) as caught:
                server.folders(path)
            self.assertEqual(caught.exception.status, 403)
        with self.assertRaises(server.FolderError) as caught:
            server.folders("code")
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(server.FolderError) as caught:
            server.folders(str(self.home / "gone"))
        self.assertEqual(caught.exception.status, 404)

    def test_a_typed_path_is_normalised_before_the_home_bound_applies(self):
        self.setenv("HOME", str(self.home))
        (self.tmp / "home2").mkdir()  # shares the home folder's name as a prefix, not as a folder
        for path in (str(self.home / "code" / ".." / "Projects"), "~/Projects", "./~/Projects",
                     str(self.home) + "//Projects/"):
            with self.subTest(path=path):
                view = server.folders(path)
                self.assertEqual((view["path"], view["parts"]), (str((self.home / "Projects").resolve()), ["Projects"]))
        for path, status in ((str(self.home / "code" / ".." / ".secrets"), 403), (str(self.tmp / "home2"), 403),
                             (str(self.home / ".." / "home2"), 403), ("~/../outside", 403), ("../home/code", 400)):
            with self.subTest(path=path), self.assertRaises(server.FolderError) as caught:
                server.folders(path)
            self.assertEqual(caught.exception.status, status)

    def test_links_are_judged_by_where_they_lead(self):
        (self.home / "code" / "escape").symlink_to(self.tmp / "outside")
        (self.home / "code" / "shortcut").symlink_to(self.home / "Projects")
        (self.home / "hidden-link").symlink_to(self.home / ".secrets")
        self.assertEqual([row["name"] for row in server.folders(str(self.home / "code"))["folders"]],
                         ["atlas", "notes", "shortcut"])
        self.assertNotIn("hidden-link", [row["name"] for row in server.folders(None)["folders"]])
        for path in (self.home / "code" / "escape", self.home / "hidden-link"):
            with self.subTest(path=path), self.assertRaises(server.FolderError):
                server.folders(str(path))

    @unittest.skipIf(os.geteuid() == 0, "root reads every folder")
    def test_an_unreadable_folder_says_so_and_lists_nothing(self):
        locked = self.home / "locked"
        (locked / "inner").mkdir(parents=True)
        locked.chmod(0o300)
        self.addCleanup(locked.chmod, 0o700)
        self.assertEqual(server.folders(str(locked)),
                         {"path": str(locked.resolve()), "parts": ["locked"], "readable": False, "folders": []})


class TestProjectsFolder(FolderCase):
    def test_the_environment_is_the_initial_value_until_the_operator_chooses(self):
        self.patch(config, "PROJECT_ROOTS", [self.home / "Projects", Path("/srv/work")])
        self.patch(server, "restart_status", return_value=None)
        self.assertEqual(config.project_roots(), [self.home / "Projects", Path("/srv/work")])

        self.assertEqual(server.save_projects_folder({"path": str(self.home / "code")}), {"roots": ["~/code"]})

        self.assertEqual(config.project_roots(), [self.home / "code"])
        self.assertEqual(server.overview()["roots"], ["~/code"])
        folders = {row["folder"] for row in config.discover_projects() if not row["managed"]}
        self.assertEqual(folders, {"atlas", "notes"})
        server.save_projects_folder({"path": ""})
        self.assertEqual(config.project_roots(), [self.home / "Projects", Path("/srv/work")])

    def test_only_the_operator_sets_an_existing_absolute_folder(self):
        with self.assertRaisesRegex(dispatch.T.TransitionError, "operator"):
            dispatch.request_setting(None, "projects_folder", str(self.home / "code"), "test", actor="l3")
        for value in ("code", str(self.home / "missing"), str(self.home / "code" / "README.md"), 3):
            with self.subTest(value=value), self.assertRaises(dispatch.T.TransitionError):
                dispatch.request_setting(None, "projects_folder", value, "test", actor=config.OPERATOR_ACTOR)
        with self.assertRaises(ValueError):
            server.save_projects_folder({"path": str(self.home / "code"), "extra": 1})

    def test_any_existing_absolute_folder_is_stored_as_typed_with_home_expanded(self):
        """Browsing stays inside home, but a typed projects folder may be anywhere the operator can read."""
        self.setenv("HOME", str(self.home))
        for typed, stored in (("~/code", self.home / "code"), ("./~/code", self.home / "code"),
                              (str(self.tmp / "outside") + "/", self.tmp / "outside"), (str(self.home / ".secrets"), self.home / ".secrets"),
                              (str(self.home / "code" / ".." / "Projects"), self.home / "code" / ".." / "Projects")):
            with self.subTest(typed=typed):
                server.save_projects_folder({"path": typed})
                self.assertEqual(config.machine_settings()["projects_folder"], str(stored))
        for typed in ("code", "../code", str(self.home / "missing")):
            with self.subTest(typed=typed), self.assertRaises(dispatch.T.TransitionError):
                server.save_projects_folder({"path": typed})

    @unittest.skipIf(os.geteuid() == 0, "root reads every folder")
    def test_an_unreadable_projects_folder_is_refused_and_never_breaks_discovery(self):
        locked = self.home / "locked"
        locked.mkdir()
        locked.chmod(0o300)
        self.addCleanup(locked.chmod, 0o700)
        with self.assertRaisesRegex(dispatch.T.TransitionError, "not readable"):
            dispatch.request_setting(None, "projects_folder", str(locked), "test", actor=config.OPERATOR_ACTOR)
        dispatch.request_setting(None, "projects_folder", str(self.home / "code"), "test", actor=config.OPERATOR_ACTOR)
        (self.home / "code").chmod(0o300)
        self.addCleanup((self.home / "code").chmod, 0o700)
        result = dispatch.run_settings()["projects_folder"]
        self.assertEqual((result["status"], result["note"]), ("refused", f"{self.home / 'code'} is not readable"))
        self.patch(config, "PROJECT_ROOTS", [locked])
        self.assertIn(self.project, {row["name"] for row in config.discover_projects()})

    def test_cli_sets_through_altd_and_shows_the_folder(self):
        result = self.alt("machine", "set", "--projects-folder", str(self.home / "code"), "--reason", "test",
                          env={"ALTITUDE_ACTOR": config.OPERATOR_ACTOR})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["request"]["projects_folder"], str(self.home / "code"))
        self.assertEqual(dispatch.run_settings()["projects_folder"]["status"], "done")
        shown = self.alt("machine", "show")
        self.assertEqual(json.loads(shown.stdout)["projects_folder"], [str(self.home / "code")])
        unset = self.alt("machine", "set", "--unset-projects-folder", "--reason", "test",
                         env={"ALTITUDE_ACTOR": config.OPERATOR_ACTOR})
        self.assertEqual(unset.returncode, 0, unset.stderr)
        dispatch.run_settings()
        self.assertEqual(config.project_roots(), config.PROJECT_ROOTS)


if __name__ == "__main__":
    unittest.main()
