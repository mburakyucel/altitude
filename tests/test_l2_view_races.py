"""#302: task polling tolerates inbox consumption and archival without inventing handoff evidence."""
from pathlib import Path
import threading
from unittest import mock

from tests.support import AltitudeCase, make_repo
from tests.fakes import FakeL2
from altitude import config, dispatch, server, state as S, tasks as T


class TestL2ViewRaces(AltitudeCase):
    def setUp(self):
        super().setUp()
        make_repo(self.repo)
        self.private_ledgers()
        self.quiet_engines()
        self.engine = FakeL2()
        self.engine.install(self)
        self.patch(server.monitor, "sessions", return_value=[])

    def launch(self, engine):
        self.register(self.project, routing=[[{"engine": engine, "model": "fixture-model"}]], wip=20)
        task = T.new(self.project, "Polling the inbox", "Preserve the saved conversation.", paths=["README.md"])
        dispatch.run(self.project, task["slug"])
        return S.load_task(self.project, task["slug"])

    def race(self, task, action):
        inbox = S.task_dir(self.project, task["slug"]) / "inbox.jsonl"
        read_text = Path.read_text
        intercepted = []
        attempted, finished = threading.Event(), threading.Event()
        errors = []

        def write():
            attempted.set()
            try:
                action()
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        writer = threading.Thread(target=write)

        def read(path, *args, **kwargs):
            if path == inbox and not intercepted:
                intercepted.append(True)
                writer.start()
                self.assertTrue(attempted.wait(5))
                self.assertFalse(finished.is_set(), "the writer must wait for the locked message snapshot")
            return read_text(path, *args, **kwargs)

        try:
            with mock.patch.object(Path, "read_text", new=read):
                view = server.task_view(self.project, task["slug"])
        finally:
            if writer.ident:
                writer.join(5)
        self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(intercepted, [True])
        return view

    def test_consuming_or_archiving_inbox_during_poll_keeps_history_without_false_delivery(self):
        for engine in config.ENGINES:
            for operation in ("take", "claim", "archive"):
                with self.subTest(engine=engine, operation=operation):
                    task = self.launch(engine)
                    message = T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, "Keep this correction.")
                    if operation == "claim":
                        T.block(self.project, task["slug"], "Operational wait")
                        action = lambda: T.claim_resume(self.project, task["slug"])
                    elif operation == "archive":
                        # Explicit rejection can archive queued steering; ordinary completion must continue it.
                        action = lambda: T.reject(self.project, task["slug"], "Operator ended this task")
                    else:
                        action = lambda: T.take_inbox(self.project, task["slug"])
                    view = self.race(task, action)
                    self.assertEqual([(row["id"], row["text"]) for row in view["messages"]],
                                     [(message["id"], message["text"])])
                    self.assertEqual(view["messages"][0]["delivery"], {
                        "state": "queued", "at": None, "removable": True,
                        "send_now": operation != "claim", "send_now_pending": False,
                        "send_now_reason": "Send now needs a running owner." if operation == "claim" else None})
                    settled = server.task_view(self.project, task["slug"])
                    self.assertEqual(settled["messages"][0]["delivery"]["state"],
                                     "sending" if operation == "claim" else "queued" if operation == "archive" else "unconfirmed")
                    self.assertFalse(settled["messages"][0]["delivery"]["removable"])
                    self.assertFalse(settled["messages"][0]["delivery"].get("send_now", False))
                    with self.assertRaises(T.TransitionError):
                        T.remove_message(self.project, task["slug"], message["id"])
                    if operation == "archive":
                        self.assertEqual(settled["state"], "rejected")
                        self.assertTrue((S.archive_dir(self.project) / task["slug"] / "inbox.jsonl").exists())

    def test_known_delivery_survives_a_later_inbox_consumption_race(self):
        for engine in config.ENGINES:
            with self.subTest(engine=engine):
                task = self.launch(engine)
                T.block(self.project, task["slug"], "Operational wait")
                delivered = T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, "Delivered correction.")
                dispatch.resume(self.project, task["slug"])
                queued = T.message(self.project, task["slug"], T.OPERATOR_MESSAGE_ROLE, "Later correction.")
                view = self.race(task, lambda: T.take_inbox(self.project, task["slug"]))
                self.assertEqual([row["id"] for row in view["messages"]], [delivered["id"], queued["id"]])
                self.assertEqual([row["delivery"]["state"] for row in view["messages"]], ["delivered", "queued"])
                settled = server.task_view(self.project, task["slug"])
                self.assertEqual([row["delivery"]["state"] for row in settled["messages"]], ["delivered", "unconfirmed"])

    def test_corrupt_and_denied_inbox_reads_still_fail_explicitly(self):
        task = self.launch(config.ENGINES[0])
        inbox = S.task_dir(self.project, task["slug"]) / "inbox.jsonl"
        inbox.write_text("{corrupt}\n")
        with self.assertRaisesRegex(ValueError, "corrupt task inbox"):
            server.task_view(self.project, task["slug"])
        read_text = Path.read_text

        def denied(path, *args, **kwargs):
            if path == inbox:
                raise PermissionError("fixture inbox denied")
            return read_text(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", new=denied), self.assertRaisesRegex(PermissionError, "inbox denied"):
            server.task_view(self.project, task["slug"])
