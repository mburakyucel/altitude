"""Recent/history/delta reads use real task storage and deterministic native source records."""
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from altitude import state as S, transcript
from tests.test_live_transcript import _CodexTranscriptCase, _stamp


class TestTranscriptProtocol(_CodexTranscriptCase):

    def message(self, index, *, text=None, at=None):
        return {"type": "item.completed", "timestamp": at or _stamp(1, index),
                "item": {"id": f"message-{index}", "type": "agent_message", "text": text or f"message {index}"}}

    def write(self, records, *, append=False, path=None):
        with (path or self.old).open("a" if append else "w") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\n")

    def all_history(self, first):
        events = list(first["events"])
        page = first
        while page["has_earlier"]:
            page = self._view(mode="history", before=page["lower"], cursor=first["cursor"])
            self.assertFalse(page["reset"])
            events = page["events"] + events
        return events

    def test_recent_transfer_is_bounded_and_history_is_complete_without_duplicate_rows(self):
        self.write([self.message(index) for index in range(220)])
        first = self._view()
        self.assertEqual(len(first["events"]), 50)
        self.assertEqual(first["events"][-1]["text"], "message 219")
        self.assertLessEqual(len(json.dumps(first).encode()), transcript.PAGE_BYTES)
        events = self.all_history(first)
        self.assertEqual([row["text"] for row in events if row["kind"] == "message"],
                         [f"message {index}" for index in range(220)])
        self.assertEqual(len({row["id"] for row in events}), len(events))
        self.assertEqual([row["order"] for row in events], sorted(row["order"] for row in events))
        self.write([self.message(index) for index in range(220, 620)], append=True)
        larger = self._view()
        self.assertEqual(len(larger["events"]), 50)
        self.assertLessEqual(len(json.dumps(larger).encode()), transcript.PAGE_BYTES)

    def test_late_and_untimed_appends_reach_loaded_interval_in_chronological_order(self):
        self.write([self.message(index) for index in range(100)])
        first = self._view()
        self.write([self.message(101, text="late inside", at=_stamp(1, 80)),
                    self.message(102, text="late before", at=_stamp(-30))], append=True)
        delta = self._view(mode="delta", cursor=first["cursor"], lower=first["lower"])
        self.assertEqual([row["text"] for row in delta["events"]], ["late inside"])
        history = self.all_history(self._view())
        self.assertIn("late before", [row["text"] for row in history])
        current = self._view()
        untimed = self.message(103, text="untimed at earliest")
        untimed.pop("timestamp")
        self.write([self.message(104, text="new earliest", at=_stamp(-40)), untimed], append=True)
        delta = self._view(mode="delta", cursor=current["cursor"], lower="")
        self.assertEqual([row["text"] for row in delta["events"]], ["new earliest", "untimed at earliest"])
        self.assertFalse(delta["has_earlier"])

    def test_changing_old_command_is_an_upsert_and_duplicate_native_ids_are_scoped_to_turn(self):
        def command(status, output):
            return {"type": "item.completed", "timestamp": _stamp(1),
                    "item": {"id": "same-id", "type": "command_execution", "command": "true",
                             "status": status, "aggregated_output": output, "exit_code": 0}}
        self.write([command("in_progress", "")])
        first = self._view()
        call = next(row for row in first["events"] if row["kind"] == "command")
        self.write([self.message(index) for index in range(1, 80)] + [command("completed", "finished")], append=True)
        delta = self._view(mode="delta", cursor=first["cursor"], lower="")
        changes = list(delta["events"])
        while delta["more"]:
            delta = self._view(mode="delta", cursor=delta["cursor"], lower="")
            changes.extend(delta["events"])
        updated = next(row for row in changes if row["kind"] == "command")
        self.assertEqual(updated["id"], call["id"])
        self.assertEqual(updated["order"], call["order"])
        self.assertEqual(updated["output"], "finished")
        self.assertGreater(updated["version"], call["version"])
        self.write([{"type": "thread.started", "thread_id": "thread-1"}, command("completed", "other turn")], path=self.new)
        calls = [row for row in self.all_history(self._view()) if row["kind"] == "command"]
        self.assertEqual({row["output"] for row in calls}, {"finished", "other turn"})
        self.assertEqual(len({row["id"] for row in calls}), 2)

    def test_bursts_are_byte_bounded_and_continuations_never_skip_equal_time_siblings(self):
        first = self._view()
        self.write([self.message(index, text=f"{index} " + "🙂" * 1900, at=_stamp(5)) for index in range(61)])
        received = []
        cursor = first["cursor"]
        for _ in range(100):
            page = self._view(mode="delta", cursor=cursor, lower="")
            self.assertFalse(page["reset"])
            self.assertLessEqual(len(json.dumps(page).encode()), transcript.PAGE_BYTES)
            received.extend(page["events"])
            self.assertNotEqual(page["cursor"], cursor)
            cursor = page["cursor"]
            if not page["more"]:
                break
        else:
            self.fail("delta did not converge")
        self.assertEqual(len(received), 61)
        self.assertEqual(len({row["id"] for row in received}), 61)
        self.assertEqual(len({row["version"] for row in received}), 61)
        quiet = self._view(mode="delta", cursor=cursor)
        self.assertEqual(quiet["events"], [])

    def test_unchanged_poll_avoids_projection_but_history_does_not_consume_delta(self):
        self.write([self.message(index) for index in range(100)])
        first = self._view()
        with patch.object(transcript, "_projection", side_effect=AssertionError("unchanged poll parsed")):
            quiet = self._view(mode="delta", cursor=first["cursor"], lower=first["lower"])
        self.assertEqual(quiet["events"], [])
        self.write([self.message(101)], append=True)
        older = self._view(mode="history", cursor=first["cursor"], before=first["lower"])
        delta = self._view(mode="delta", cursor=first["cursor"], lower=older["lower"])
        self.assertEqual([row["text"] for row in delta["events"]], ["message 101"])

    def test_expiry_and_source_replacement_reset_then_reconcile_the_requested_interval(self):
        self.write([self.message(index) for index in range(120)])
        first = self._view()
        with patch.object(transcript, "INDEX_TTL", 0):
            reset = self._view(mode="delta", cursor=first["cursor"], lower=first["lower"])
        self.assertTrue(reset["reset"])
        self.assertEqual(reset["events"], [])
        page = self._view(mode="reconcile", lower=first["lower"])
        self.assertEqual({row["id"] for row in page["events"]}, {row["id"] for row in first["events"]})
        self.assertFalse(page["more"])
        cursor = page["cursor"]
        previous = self.old.stat()
        text = self.old.read_text().replace("message 119", "changed 119")
        self.old.write_text(text)
        os.utime(self.old, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        reset = self._view(mode="delta", cursor=cursor, lower=first["lower"])
        self.assertTrue(reset["reset"], "same-size rewrite with unchanged mtime must not reuse old cursor")
        page = self._view(mode="reconcile", lower=first["lower"])
        self.assertIn("changed 119", [row["text"] for row in page["events"]])

    def test_reconciliation_uses_exclusive_forward_pages_and_catches_concurrent_appends(self):
        self.write([self.message(index) for index in range(125)])
        page = self._view(mode="reconcile", lower="")
        baseline = page["cursor"]
        events = list(page["events"])
        self.write([self.message(126, text="late during reconciliation", at=_stamp(-50))], append=True)
        while page["more"]:
            page = self._view(mode="reconcile", cursor=baseline, lower="", after=page["next"])
            self.assertFalse(page["reset"])
            events.extend(page["events"])
        delta = self._view(mode="delta", cursor=baseline, lower="")
        self.assertEqual([row["text"] for row in delta["events"]], ["late during reconciliation"])
        self.assertEqual(len({row["id"] for row in events}), len(events))

    def test_partial_completion_deletes_notice_without_reset(self):
        self.write([self.message(1)])
        with self.old.open("a") as stream:
            stream.write('{"type":"item.completed","item":{"type":"agent_message","text":"finished partial"}')
        first = self._view()
        error = next(row for row in first["events"] if row["kind"] == "error")
        with self.old.open("a") as stream:
            stream.write('}\n')
        delta = self._view(mode="delta", cursor=first["cursor"])
        self.assertFalse(delta["reset"])
        self.assertIn(error["id"], delta["deleted"])
        self.assertEqual([row["text"] for row in delta["events"]], ["finished partial"])

    def test_attempt_fence_during_materialization_never_publishes_a_stale_generation(self):
        self.write([self.message(1)])
        original = transcript._projection
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            task = S.load_task(self.project, self.slug)
            task["attempt"] = 2
            S.save_task(self.project, task)
            return result
        with patch.object(transcript, "_projection", side_effect=changed):
            with self.assertRaises(transcript.TranscriptAccessError):
                self._view()
        with self.assertRaises(transcript.TranscriptAccessError):
            self._view(mode="delta", cursor="bad")

    def test_concurrent_reads_publish_identical_versions_once(self):
        self.write([self.message(index) for index in range(80)])
        first = self._view()
        self.write([self.message(81)], append=True)
        barrier = threading.Barrier(4)
        def poll(_):
            barrier.wait()
            return self._view(mode="delta", cursor=first["cursor"], lower=first["lower"])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(poll, range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual([row["text"] for row in results[0]["events"]], ["message 81"])

    def test_raw_disclosure_chunks_and_rejects_stale_record_without_leaking_reasoning(self):
        self.write([self.message(1, text="🙂" * 12000),
                    {"type": "item.completed", "item": {"type": "reasoning", "text": "PRIVATE"}}])
        first = self._view(raw=True)
        event = next(row for row in first["events"] if row["kind"] == "message" and row["source"] != "platform")
        self.assertLessEqual(len(json.dumps(first).encode()), transcript.PAGE_BYTES)
        text = ""
        offset = 0
        while offset is not None:
            chunk = self._view(raw=True, mode="record", record=event["id"], offset=offset)
            self.assertLessEqual(len(json.dumps(chunk).encode()), transcript.PAGE_BYTES)
            self.assertLessEqual(len(chunk["text"]), 4000)
            text += chunk["text"]
            offset = chunk["next_offset"]
        self.assertEqual(json.loads(text)["item"]["text"], "🙂" * 12000)
        self.assertNotIn("PRIVATE", json.dumps(first))
        self.write([self.message(1, text="replacement")])
        with self.assertRaises(transcript.TranscriptAccessError):
            self._view(raw=True, mode="record", record=event["id"])

    def test_changed_source_during_read_is_not_published_and_retry_observes_it(self):
        self.write([self.message(1)])
        original = transcript._projection
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.write([self.message(2)], append=True)
            return result
        with patch.object(transcript, "_projection", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed while reading"):
                self._view()
        self.assertEqual([row["text"] for row in self._view()["events"] if row["kind"] == "message"],
                         ["message 1", "message 2"])

    def test_tombstone_exhaustion_requests_reconciliation_instead_of_losing_deletions(self):
        self.write([self.message(1)])
        with self.old.open("a") as stream:
            stream.write('{"type":"item.completed","item":{"type":"agent_message","text":"complete"}')
        first = self._view()
        with self.old.open("a") as stream:
            stream.write('}\n')
        with patch.object(transcript, "TOMBSTONE_LIMIT", 0):
            page = self._view(mode="delta", cursor=first["cursor"])
        self.assertTrue(page["reset"])
        self.assertIn("complete", [row["text"] for row in self._view(mode="reconcile")["events"]])

    def test_same_attempt_resume_invalidates_fingerprint_without_resetting_session(self):
        self.write([self.message(1)])
        first = self._view()
        task = S.load_task(self.project, self.slug)
        task["agent_id"] = "w3"
        S.save_task(self.project, task)
        third = self._turn("w3", _stamp(20), "thread-1", resume=True)
        self.write([self.message(2, at=_stamp(20))], path=third)
        page = self._view(mode="delta", cursor=first["cursor"], lower="")
        self.assertFalse(page["reset"])
        self.assertIn("message 2", [row["text"] for row in page["events"]])
        self.assertEqual(page["cursor"].split(":")[0], first["cursor"].split(":")[0])

    def test_same_time_source_replacement_keeps_loaded_order_boundary_meaning(self):
        self.write([self.message(index, at=_stamp(5)) for index in range(90)])
        first = self._view()
        replacement = self.old.with_suffix(".replacement")
        replacement.write_bytes(self.old.read_bytes())
        replacement.replace(self.old)
        reset = self._view(mode="delta", cursor=first["cursor"], lower=first["lower"])
        self.assertTrue(reset["reset"])
        page = self._view(mode="reconcile", lower=first["lower"])
        self.assertEqual([row["text"] for row in page["events"]], [row["text"] for row in first["events"]])
        self.assertEqual([row["order"] for row in page["events"]], [row["order"] for row in first["events"]])
        self.assertNotEqual(page["events"][0]["id"], first["events"][0]["id"])

    def test_invalid_external_tokens_cannot_expand_or_stall_a_page(self):
        for arguments in ({"before": "x"}, {"lower": "0" * 100000}, {"after": "z" * 73},
                          {"cursor": "x" * 65}, {"record": "../status.json"}, {"offset": -1}):
            with self.subTest(arguments=list(arguments)):
                with self.assertRaises(ValueError):
                    self._view(**arguments)

    def test_index_contains_only_signatures_and_orders_and_is_lru_bounded(self):
        marker = "body must never live in index"
        self.write([self.message(1, text=marker)])
        self._view()
        index = next(reversed(transcript._indexes.values()))
        self.assertNotIn(marker, repr(index))
        self.assertLessEqual(len(transcript._indexes), transcript.INDEX_LIMIT)
        self.assertTrue(index.rows)


if __name__ == "__main__":
    unittest.main()
