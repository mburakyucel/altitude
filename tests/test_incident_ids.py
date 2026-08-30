"""Incident ids are unique under concurrent faults (I-013). Two `l2-died` faults raised in the same second both
counted the same rows, both got `I-009`, and the blind write kept only the second record. The id now comes from
the highest id ever issued and the file is reserved with O_EXCL, so a race loses nothing. Runs against a
throwaway ALTITUDE_HOME; every test builds its own temp home, never the live ledger."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="altitude-iid-")
REPO = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO)
from altitude import config, improve, state as S  # noqa: E402

# One racing child for the multi-process test: waits on a file barrier, then files one incident and prints its id.
CHILD = '''
import os, sys, time
sys.path.insert(0, sys.argv[1])
from altitude import improve
go, marker = sys.argv[2], sys.argv[3]
while not os.path.exists(go):
    time.sleep(0.005)
res = improve.new_incident("demo", title=marker, task=None, what=marker, evidence="events.log",
                           cause="not yet analysed", tags=["system-fault"])
print(res["id"])
'''


class TempHome:
    """config.ROOT and the paths derived from it at import time are patched per test: `unittest discover` imports
    several modules that each point ALTITUDE_HOME somewhere else, so the run-time values cannot be relied on."""

    # (module, attribute, path under the temp home) for every constant a module froze out of config.ROOT at import.
    DERIVED = ((config, "INCIDENT_INDEX", "incidents.jsonl"), (config, "PROJECTS_FILE", "projects.json"),
               (config, "MONITOR_DIR", "monitor"), (improve, "FAULTS", "monitor/faults.json"))

    def use_temp_home(self, project: str = "demo") -> Path:
        self._saved = [(config, "ROOT", config.ROOT)] + [(m, k, getattr(m, k)) for m, k, _ in self.DERIVED]
        root = Path(tempfile.mkdtemp(prefix="altitude-iid-case-"))
        config.ROOT = root
        for m, k, rel in self.DERIVED:
            setattr(m, k, root / rel)
        self.addCleanup(self._restore_home)
        repo = root / "repo"
        (repo / "docs" / "incidents").mkdir(parents=True, exist_ok=True)
        config.save_projects({project: {"name": project, "path": str(repo), "stacks": ["python"]}})
        return root

    def _restore_home(self):
        for m, k, v in self._saved:
            setattr(m, k, v)


def ledger_row(iid: str, project: str = "demo") -> str:
    return json.dumps({"at": "2026-08-29T00:00:00", "project": project, "id": iid, "title": iid, "task": None,
                       "tags": [], "scope": "project", "mechanism": "incident-only", "rule": None, "cause": ""},
                      sort_keys=True)


class TestNextIncidentId(TempHome, unittest.TestCase):

    def setUp(self):
        self.root = self.use_temp_home()

    def test_empty_everywhere_starts_at_one(self):
        self.assertEqual(improve.next_incident_id("demo"), "I-001")

    def test_a_gap_is_never_reused_and_a_duplicate_never_shifts_the_max(self):
        """The I-013 wreckage itself: I-009 filed twice, I-010 never issued, I-013 present. Counting rows says
        I-004; counting the folder says I-003. The next free id is I-014."""
        ledger = config.project_dir("demo") / "incidents.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("\n".join(ledger_row(i) for i in ("I-009", "I-009", "I-011", "I-013")) + "\n")
        self.assertEqual(improve.next_incident_id("demo"), "I-014")

    def test_every_source_of_ids_is_consulted(self):
        d = config.project_dir("demo") / "incidents"
        d.mkdir(parents=True, exist_ok=True)
        (d / "I-004.md").write_text("altitude-side copy")
        self.assertEqual(improve.next_incident_id("demo"), "I-005")
        (Path(config.project_path("demo")) / "docs" / "incidents" / "I-021.md").write_text("repo copy")
        self.assertEqual(improve.next_incident_id("demo"), "I-022")
        config.INCIDENT_INDEX.write_text(ledger_row("I-030") + "\n")
        self.assertEqual(improve.next_incident_id("demo"), "I-031")

    def test_another_projects_ids_do_not_count(self):
        config.INCIDENT_INDEX.write_text(ledger_row("I-099", project="other") + "\n" + ledger_row("I-002") + "\n")
        self.assertEqual(improve.next_incident_id("demo"), "I-003")

    def test_unparseable_ids_are_skipped_not_crashed_on(self):
        ledger = config.project_dir("demo") / "incidents.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("{not json\n" + json.dumps(["not", "a", "row"]) + "\n"
                          + ledger_row("I-00X") + "\n" + ledger_row("R-007") + "\n" + ledger_row("I-006") + "\n")
        d = config.project_dir("demo") / "incidents"
        d.mkdir(parents=True, exist_ok=True)
        (d / "I-draft.md").write_text("not an id")
        self.assertEqual(improve.next_incident_id("demo"), "I-007")


class TestReserveIncidentFile(TempHome, unittest.TestCase):

    def setUp(self):
        self.root = self.use_temp_home()

    def file_incident(self, what: str) -> dict:
        return improve.new_incident("demo", title=what, task=None, what=what, evidence="events.log",
                                    cause="not yet analysed", tags=["system-fault"])

    def test_an_existing_incident_file_is_never_clobbered(self):
        d = config.project_dir("demo") / "incidents"
        d.mkdir(parents=True, exist_ok=True)
        (d / "I-001.md").write_text("SQUATTER — hand-written, must survive")
        res = self.file_incident("second incident")
        self.assertEqual(res["id"], "I-002")
        self.assertEqual((d / "I-001.md").read_text(), "SQUATTER — hand-written, must survive")
        self.assertIn("second incident", (d / "I-002.md").read_text())

    def test_a_squatter_on_every_candidate_id_raises_and_names_it(self):
        """When allocation keeps landing on a file that already exists, it gives up loudly rather than overwrite."""
        d = config.project_dir("demo") / "incidents"
        d.mkdir(parents=True, exist_ok=True)
        (d / "I-001.md").write_text("squatter")
        old = improve.next_incident_id
        improve.next_incident_id = lambda project: "I-001"
        try:
            with self.assertRaises(RuntimeError) as e:
                self.file_incident("doomed")
        finally:
            improve.next_incident_id = old
        self.assertIn("I-001", str(e.exception))
        self.assertIn("already exists", str(e.exception))
        self.assertEqual((d / "I-001.md").read_text(), "squatter")

    def test_a_lost_race_retries_onto_the_next_free_id(self):
        """The FileExistsError branch, deterministically: the first candidate is already taken (as it would be if a
        racer reserved it between our max and our open), so allocation recomputes and takes the next one."""
        d = config.project_dir("demo") / "incidents"
        d.mkdir(parents=True, exist_ok=True)
        (d / "I-001.md").write_text("taken by a racer")
        real, calls = improve.next_incident_id, []
        improve.next_incident_id = lambda project: (calls.append(project), "I-001" if len(calls) == 1 else real(project))[1]
        try:
            res = self.file_incident("retried")
        finally:
            improve.next_incident_id = real
        self.assertEqual(len(calls), 2, "the collision must have driven exactly one retry")
        self.assertEqual(res["id"], "I-002")
        self.assertEqual((d / "I-001.md").read_text(), "taken by a racer")
        self.assertIn("retried", (d / "I-002.md").read_text())

    def test_filing_an_incident_under_the_project_lock_does_not_deadlock(self):
        """`dispatch.run` holds `S.project_lock(project)` while `wip_hold` files a `quota-unknown` fault, so
        allocation must never take that same flock — it is not reentrant, and a nested take would hang altd while
        holding the lock. The watchdog turns that hang into a failure instead of wedging the suite."""
        def watchdog(signum, frame):
            raise AssertionError("new_incident blocked while the caller held the project lock — nested flock (I-013)")

        previous = signal.signal(signal.SIGALRM, watchdog)
        signal.setitimer(signal.ITIMER_REAL, 10)
        try:
            with S.project_lock("demo"):
                res = self.file_incident("filed while the project lock was held")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        self.assertEqual(res["id"], "I-001")
        self.assertIn("filed while the project lock was held",
                      (config.project_dir("demo") / "incidents" / "I-001.md").read_text())

    def race(self, workers: int, rounds: int) -> list[str]:
        """N threads released together by a barrier, several rounds. Every incident must end up with its own id and
        its own file, with its own text still in it."""
        d = config.project_dir("demo") / "incidents"
        ids, errors = [], []
        lock = threading.Lock()
        for rnd in range(rounds):
            barrier = threading.Barrier(workers)
            bodies = {}

            def run(i, rnd=rnd):
                marker = f"racer r{rnd}w{i}"
                barrier.wait()
                try:
                    res = self.file_incident(marker)
                except BaseException as e:                       # noqa: BLE001 — the test reports, never swallows
                    with lock:
                        errors.append(repr(e))
                    return
                with lock:
                    ids.append(res["id"])
                    bodies[res["id"]] = marker

            threads = [threading.Thread(target=run, args=(i,)) for i in range(workers)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [], "no racer may fail")
            for iid, marker in bodies.items():
                self.assertIn(marker, (d / f"{iid}.md").read_text(), f"{iid} lost its body to another racer")

        self.assertEqual(len(ids), rounds * workers)
        self.assertEqual(len(set(ids)), len(ids), f"duplicate incident ids allocated: {sorted(ids)}")
        self.assertEqual(sorted(ids), [f"I-{n:03d}" for n in range(1, len(ids) + 1)])
        self.assertEqual(sorted(p.stem for p in d.glob("I-*.md")), sorted(ids))
        self.assertEqual(improve.next_incident_id("demo"), f"I-{len(ids) + 1:03d}")
        return ids

    def test_concurrent_incidents_get_distinct_ids_and_keep_every_body(self):
        self.race(workers=6, rounds=4)

    def test_racers_still_get_distinct_ids_with_the_allocation_lock_disabled(self):
        """With the lock a racer waits its turn, so the O_EXCL retry almost never fires. Take the lock away — it is
        ours, so this is a one-liner — and the threads collide on the exclusive create for real: uniqueness must
        come out of O_EXCL plus the retry alone, exactly as it must across two processes."""
        @contextmanager
        def no_lock(directory):
            directory.mkdir(parents=True, exist_ok=True)
            yield

        real = improve._alloc_lock
        improve._alloc_lock = no_lock
        self.addCleanup(lambda: setattr(improve, "_alloc_lock", real))
        self.race(workers=8, rounds=4)

    def test_separate_processes_racing_on_one_home_get_distinct_ids(self):
        """The real I-013 shape: two independent processes filing a fault at the same moment. Children share one
        ALTITUDE_HOME and are released by a file barrier, so the flock and the exclusive create are doing the work
        across process boundaries, not just across threads."""
        workers = 6
        script = self.root / "racer.py"
        script.write_text(CHILD)
        go = self.root / "go"
        env = dict(os.environ, ALTITUDE_HOME=str(self.root))
        procs = [subprocess.Popen([sys.executable, str(script), REPO, str(go), f"child {i}"], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(workers)]
        time.sleep(0.3)                                     # let every child reach the barrier before the gun
        go.write_text("go")
        out = [p.communicate(timeout=120) for p in procs]
        for p, (stdout, stderr) in zip(procs, out):
            self.assertEqual(p.returncode, 0, f"child failed: {stderr}")
        ids = [stdout.strip() for stdout, _ in out]
        self.assertEqual(len(set(ids)), workers, f"duplicate incident ids across processes: {sorted(ids)}")
        self.assertEqual(sorted(ids), [f"I-{n:03d}" for n in range(1, workers + 1)])
        d = self.root / "demo" / "incidents"
        for i, iid in enumerate(ids):
            self.assertIn(f"child {i}", (d / f"{iid}.md").read_text(), f"{iid} lost its body to another process")


if __name__ == "__main__":
    unittest.main()
