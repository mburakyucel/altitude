"""The task door admits a worker's record change only from the task's current worker job and attempt, applies it to
that task alone, and applies a repeated request once (docs/ARCHITECTURE.md#task-door)."""
import http.client
import json
import os
import threading
import time
import uuid
from unittest import mock

from tests.support import AltitudeCase, door_port
from altitude import dispatch, door, engines, server, state as S, tasks as T


class TaskDoor(AltitudeCase):
    door = False  # these cases decide who the task's owner is

    def setUp(self):
        super().setUp()
        self.serving(door_port())
        self.owner = self.patch(server, "task_owner_connection", return_value=True)
        task = T.new(self.project, "Door owner", "Reply through the door.")
        self.slug = task["slug"]
        task.update(state="running", attempt=2, agent_id="worker")
        S.save_task(self.project, task)

    def post(self, **fields):
        body = {"project": self.project, "slug": self.slug, "attempt": "2", "kind": "alt",
                "request": uuid.uuid4().hex, "args": ["task", "reply", "Progress."], **fields}
        body = {key: value for key, value in body.items() if value is not None}
        connection = http.client.HTTPConnection("127.0.0.1", door_port(), timeout=60)
        try:
            connection.request("POST", "/api/task/door", body=json.dumps(body),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def replies(self):
        return [row["text"] for row in T.task_messages(self.project, self.slug) if row["role"] == "l2"]

    def test_only_the_current_worker_job_and_attempt_are_admitted(self):
        for fields, owner in (({"attempt": "1"}, True), ({"attempt": True}, True), ({}, False),
                              ({"slug": "another-task"}, True), ({"project": "another-project"}, True)):
            with self.subTest(fields=fields, owner=owner):
                self.owner.return_value = owner
                self.assertEqual(self.post(**fields), (403, {"error": server.TASK_DOOR_REFUSED}))
        self.assertEqual(self.replies(), [])
        self.owner.return_value = True
        status, answer = self.post()
        self.assertEqual((status, answer["returncode"]), (200, 0), answer)
        self.assertEqual(self.replies(), ["Progress."])
        self.assertEqual(self.owner.call_args.args[:2], (self.project, self.slug))
        # The worker's last record changes, as its stop checkpoint after a block, still reach its task.
        T.block(self.project, self.slug, "Which copy?")
        status, answer = self.post(args=["task", "reply", "Waiting for the answer."])
        self.assertEqual((status, answer["returncode"]), (200, 0), answer)
        self.assertEqual(self.replies()[-1], "Waiting for the answer.")

    def test_a_verb_acts_only_on_the_admitted_task(self):
        other = T.new(self.project, "Another task", "Not the owner's.")
        other.update(state="running", attempt=2)
        S.save_task(self.project, other)
        for args in (["task", "reply", other["slug"], "Elsewhere."], ["--project", "another-project", "task", "reply", "x"],
                     ["task", "block", other["slug"], "--reason", "Elsewhere?"]):
            with self.subTest(args=args):
                status, answer = self.post(args=args)
                self.assertEqual((status, answer["returncode"]), (200, 1), answer)
        self.assertEqual(T.task_messages(self.project, other["slug"]), [])
        self.assertEqual(S.load_task(self.project, other["slug"])["state"], "running")

    def test_the_door_runs_no_command_of_the_workers_own(self):
        for args in (["land", "--message", "Elsewhere."], ["task", "status"], ["task", "run", self.slug, "true"]):
            with self.subTest(args=args):
                status, answer = self.post(args=args)
                self.assertEqual((status, answer["returncode"]), (200, 1), answer)
                self.assertIn("the task door runs only verbs that change the task's records", answer["stderr"])

    def test_a_checkpoint_asked_again_delivers_its_messages_once(self):
        T.message(self.project, self.slug, "l3", "Rebase before landing.")
        request = uuid.uuid4().hex
        first = self.post(kind="inbox", request=request, args=None, stop=False, background=[])
        self.assertEqual(first[0], 200)
        self.assertIn("Rebase before landing.", first[1]["text"])
        self.assertEqual(T.pending(self.project, self.slug), [])
        # The answer was lost on the way back: asked again, the same messages arrive, taken once.
        self.assertEqual(self.post(kind="inbox", request=request, args=None, stop=False, background=[]), first)

    def test_a_driver_takes_only_the_message_altd_handed_it(self):
        sends = engines.common._worker_paths(dispatch.l2_job_root(self.project, self.slug), "worker")["sends"]
        record = sends.parent / "worker.json"
        sends.mkdir(parents=True)
        record.write_text(json.dumps({"id": "worker", "sends": str(sends)}))
        (sends / "notes.json").write_text(json.dumps({"id": "notes", "text": "Not handed to the driver."}))
        status, answer = self.post(kind="claim", args=None, message="notes")
        self.assertEqual((status, answer), (200, {"message": None}))
        self.assertTrue((sends / "notes.json").exists())
        message = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Use the staging copy.")
        with S.project_lock(self.project):
            T.claim_send_now(self.project, S.load_task(self.project, self.slug), [message], sends)
        # Another message in the folder is still not the one handed over.
        self.assertEqual(self.post(kind="claim", args=None, message="notes"), (200, {"message": None}))
        self.assertTrue((sends / "notes.json").exists())
        # The ownership record names another folder: altd keeps to the layout's own.
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        record.write_text(json.dumps({"id": "worker", "sends": str(elsewhere)}))
        status, answer = self.post(kind="claim", args=None, message=message["id"])
        self.assertEqual(answer["message"]["id"], message["id"])
        self.assertTrue((sends / f"{message['id']}.writing").exists())
        # A link where the sends folder was is not followed.
        os.rename(sends, self.tmp / "moved")
        sends.symlink_to(elsewhere)
        (elsewhere / f"{message['id']}.writing").write_text(json.dumps({"id": "forged"}))
        self.assertEqual(self.post(kind="claim", args=None, message=message["id"])[0], 409)

    def test_outcomes_are_kept_while_a_client_may_ask_again(self):
        ledger = S.task_dir(self.project, self.slug) / door.LEDGER
        self.assertEqual(self.post()[0], 200)
        old, recent = ledger / f"{'c' * 32}.json", ledger / f"{'d' * 32}.json"
        for path in (old, recent):
            S.write_json(path, {"at": S.now(), "answer": {}})
        past = time.time() - 2 * (door.WAIT + door.TIMEOUT) - 60
        os.utime(old, (past, past))
        self.assertEqual(self.post()[0], 200)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_a_repeated_request_is_applied_once_and_an_interrupted_one_is_unknown(self):
        request = uuid.uuid4().hex
        first = self.post(request=request)
        self.assertEqual(first[0], 200)
        self.assertEqual(self.post(request=request), first)
        self.assertEqual(self.replies(), ["Progress."])
        # A restart cut this request off after altd took it: its effect is unknown, so it is refused, not repeated.
        interrupted = uuid.uuid4().hex
        S.write_json(S.task_dir(self.project, self.slug) / door.LEDGER / f"{interrupted}.json", {"at": S.now()})
        self.assertEqual(self.post(request=interrupted), (409, {"error": door.UNKNOWN}))
        self.assertEqual(self.replies(), ["Progress."])

    def test_a_repeat_waits_for_the_request_still_running(self):
        started, release, calls = threading.Event(), threading.Event(), []

        def apply():
            calls.append(1)
            started.set()
            release.wait(10)
            return {"done": len(calls)}

        answers = []
        first = threading.Thread(target=lambda: answers.append(door.once(self.project, self.slug, "a" * 32, apply)))
        first.start()
        self.assertTrue(started.wait(10))
        second = threading.Thread(target=lambda: answers.append(door.once(self.project, self.slug, "a" * 32, apply)))
        second.start()
        release.set()
        first.join(10)
        second.join(10)
        self.assertEqual((answers, calls), ([{"done": 1}, {"done": 1}], [1]))

    def test_altd_never_opens_a_path_the_worker_names(self):
        missing = str(self.tmp / "not-on-this-side.txt")
        status, answer = self.post(args=["task", "reply", "--file", missing],
                                   files={missing: door.encode(b"Sent as content.")})
        self.assertEqual((status, answer["returncode"]), (200, 0), answer)
        self.assertEqual(self.replies(), ["Sent as content."])
        status, answer = self.post(args=["task", "reply", "--file", missing])
        self.assertEqual(answer["returncode"], 1)
        self.assertEqual(self.replies(), ["Sent as content."])

    def test_unknown_kinds_and_fields_are_refused(self):
        for fields in ({"kind": "anything"}, {"extra": 1}, {"request": "short"},
                       {"kind": "land", "op": "notify", "fields": {}, "extra": 1}):
            with self.subTest(fields=fields):
                self.assertEqual(self.post(**fields)[0], 409)
        with mock.patch.object(door, "run_alt") as run:
            self.assertEqual(self.post(kind="inbox", stop="yes", background="x")[0], 409)
        run.assert_not_called()

    def worker_sends(self, agent_id):
        sends = engines.common._worker_paths(dispatch.l2_job_root(self.project, self.slug), agent_id)["sends"]
        sends.mkdir(parents=True)
        (sends.parent / f"{agent_id}.json").write_text(json.dumps({"id": agent_id, "sends": str(sends)}))
        return sends

    def test_a_driver_settles_only_the_hand_off_altd_gave_it(self):
        earlier, current = self.worker_sends("earlier"), self.worker_sends("worker")
        message = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Use the staging copy.")
        with S.project_lock(self.project):
            task = S.load_task(self.project, self.slug)
            task["agent_id"] = "earlier"
            T.claim_send_now(self.project, task, [message], earlier)
            task = S.load_task(self.project, self.slug)
            task["agent_id"] = "worker"
            S.save_task(self.project, task)
        (current / f"{message['id']}.writing").write_text("{}")
        # The later worker names the earlier worker's hand-off: nothing is settled, in the record or its folder.
        self.assertEqual(self.post(kind="settle", args=None, message=message["id"], outcome="delivered"), (200, {}))
        task = S.load_task(self.project, self.slug)
        self.assertEqual(task["send_now"]["agent_id"], "earlier")
        self.assertEqual(task.get("message_deliveries") or {}, {})
        self.assertEqual([row["id"] for row in T.pending(self.project, self.slug)], [message["id"]])
        self.assertTrue((current / f"{message['id']}.writing").exists())
        # The record alone decides, under the project lock: a claim another worker holds is not settled for this one.
        T.settle_send_now(self.project, self.slug, message["id"], "delivered", agent_id="worker")
        self.assertEqual(S.load_task(self.project, self.slug)["send_now"]["agent_id"], "earlier")
        # Handed to this worker, the same settlement delivers it.
        with S.project_lock(self.project):
            T.claim_send_now(self.project, S.load_task(self.project, self.slug), [message], current)
        self.assertEqual(self.post(kind="settle", args=None, message=message["id"], outcome="delivered"), (200, {}))
        task = S.load_task(self.project, self.slug)
        self.assertNotIn("send_now", task)
        self.assertEqual(task["message_deliveries"][message["id"]]["agent_id"], "worker")
        self.assertEqual(T.pending(self.project, self.slug), [])
        self.assertTrue((current / f"{message['id']}.delivered").exists())

    def test_recovery_settles_an_earlier_workers_hand_off_as_that_worker(self):
        earlier = self.worker_sends("earlier")
        message = T.message(self.project, self.slug, T.OPERATOR_MESSAGE_ROLE, "Use the staging copy.")
        with S.project_lock(self.project):
            task = S.load_task(self.project, self.slug)
            task["agent_id"] = "earlier"
            T.claim_send_now(self.project, task, [message], earlier)
            task = S.load_task(self.project, self.slug)
            task["agent_id"] = "worker"
            S.save_task(self.project, task)
        os.rename(earlier / f"{message['id']}.json", earlier / f"{message['id']}.delivered")
        T.block(self.project, self.slug, "Which copy?")
        T.claim_resume(self.project, self.slug)
        task = S.load_task(self.project, self.slug)
        self.assertNotIn("send_now", task)
        self.assertEqual((task["message_deliveries"][message["id"]]["state"],
                          task["message_deliveries"][message["id"]]["agent_id"]), ("delivered", "earlier"))

    def test_a_failed_request_is_refused_again_without_running_twice(self):
        calls = []

        def apply():
            calls.append(1)
            raise ValueError("Nothing to reply to.")

        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "^Nothing to reply to.$"):
                door.once(self.project, self.slug, "b" * 32, apply)
        self.assertEqual(calls, [1])
        saved = S.read_json(S.task_dir(self.project, self.slug) / door.LEDGER / f"{'b' * 32}.json")
        self.assertEqual(saved["error"], "Nothing to reply to.")

    def test_a_verb_request_out_of_shape_runs_nothing(self):
        good = {"args": ["task", "reply", "x"], "stdin": "", "files": {}, "shown": {}}
        for change in ({"args": "task reply x"}, {"args": ["x"] * 129}, {"args": [1]}, {"args": ["x" * 16385]},
                       {"stdin": None}, {"files": []}, {"files": {"a": 1}}, {"shown": {1: "x"}}):
            with self.subTest(change=change), mock.patch.object(door.subprocess, "run") as run:
                request = {**good, **change}
                with self.assertRaisesRegex(ValueError, "invalid alt verb request"):
                    door.run_alt(self.project, self.slug, 2, request["args"], request["stdin"], request["files"],
                                 request["shown"])
                run.assert_not_called()
        with mock.patch.object(door.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run:
            door.run_alt(self.project, self.slug, 2, ["x"] * 128, "", {}, {})
        run.assert_called_once()
        status, answer = self.post(args=["task", "reply", "x" * 16384])
        self.assertEqual((status, answer["returncode"]), (200, 0), answer)

    def test_verb_output_is_capped_at_one_mebibyte(self):
        cap, cut = 1 << 20, "\n[output truncated]\n"
        self.assertEqual(door._bounded("x" * cap), "x" * cap)
        self.assertEqual(door._bounded("x" * (cap + 1)), "x" * cap + cut)
        # A character cut at the cap is dropped, not mangled.
        self.assertEqual(door._bounded("x" * (cap - 1) + "é"), "x" * (cap - 1) + cut)
