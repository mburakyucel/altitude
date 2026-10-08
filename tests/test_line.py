"""The machine's lines: places taken in arrival order, however many free up at once."""
from contextlib import ExitStack
import threading
import time
import unittest

from altitude import line


class TestLine(unittest.TestCase):
    def test_requests_are_admitted_in_arrival_order_when_several_places_free_at_once(self):
        for _ in range(20):
            places, done = line.Line(2, "a place"), threading.Event()
            held = ExitStack()
            for n in range(2):
                held.enter_context(places.turn(f"holder {n}", None, command="test"))

            def request(name):
                with places.turn(name, None, command="test", wait=30, watch=.01):
                    done.wait(30)
            requests = []
            for name in "ABC":
                requests.append(threading.Thread(target=request, args=(name,)))
                requests[-1].start()
                while len(places._queue) < len(requests):   # each waits before the next arrives
                    time.sleep(.001)
            held.close()   # both places free together
            while len(places.holders) < 2:
                time.sleep(.001)
            with places.lock:   # holders are kept in the order they were admitted
                self.assertEqual([h["what"] for h in places.holders], ["A", "B"])
            done.set()
            for thread in requests:
                thread.join(30)
            self.assertEqual((places.holders, places._queue), ([], []))

    def test_a_forced_place_is_taken_beyond_the_line_and_freed_at_its_end(self):
        places = line.Line(1, "a place")
        with places.turn("running", None, command="test"), places.turn("recovered", 60, command="test", force=True):
            self.assertEqual([h["what"] for h in places.holders], ["running", "recovered"])
            with places.lock:
                self.assertIn("recovered until its limit at", places._waiting_for(0))
            with self.assertRaisesRegex(ValueError, r"^test: not admitted within 0 minutes, waiting for a place: "
                                        r"running, recovered until its limit at .* UTC; try again$"):
                with places.turn("late", None, command="test"):
                    pass
        self.assertEqual((places.holders, places._queue), ([], []))

    def test_a_request_whose_client_goes_away_leaves_the_line(self):
        places = line.Line(1, "a place")
        told = []
        with places.turn("running", None, command="test"):
            with self.assertRaisesRegex(ValueError, "test: its client stopped or lost its connection while it waited"):
                with places.turn("leaving", None, command="test", wait=30, waiting=told.append,
                                 gone=lambda: bool(told)):
                    pass
        self.assertEqual(told, ["waiting for a place: running"])
        self.assertEqual(places._queue, [])


if __name__ == "__main__":
    unittest.main()
