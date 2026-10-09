"""Run a slice of the unittest suite in fresh interpreters; no third-party runner required."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os
from pathlib import Path
import sys
import time
import unittest


class ShardStream:
    def __init__(self, number):
        self.prefix = f"[python {number}] "
        self.pending = ""

    def write(self, text):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            sys.stderr.write(self.prefix + line + "\n")
            sys.stderr.flush()

    def flush(self):
        if self.pending:
            sys.stderr.write(self.prefix + self.pending + "\n")
            self.pending = ""
        sys.stderr.flush()


def tests_of(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from tests_of(test)
        else:
            yield test


def run_shard(args):
    number, directory, shard, shards, worker, workers = args
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, directory)
    # Every process discovers the same sorted suite and takes every n-th test of its shard, so
    # shards and processes partition the suite and each long module spreads across all of them.
    # An import error is a test of its own and lands in exactly one slice.
    tests = sorted(tests_of(unittest.defaultTestLoader.discover(directory)), key=lambda test: test.id())
    suite = unittest.TestSuite(tests[shard - 1::shards][worker::workers])
    result = unittest.TextTestRunner(stream=ShardStream(number), verbosity=2).run(suite)
    return {
        "ran": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expected failures": len(result.expectedFailures),
        "unexpected successes": len(result.unexpectedSuccesses),
    }


def shard_of(value):
    shard, _, shards = value.partition("/")
    if not (shard.isdigit() and shards.isdigit() and 1 <= int(shard) <= int(shards)):
        raise argparse.ArgumentTypeError("expected i/n with 1 <= i <= n")
    return int(shard), int(shards)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=max(1, len(os.sched_getaffinity(0)) // 2)
                        if hasattr(os, "sched_getaffinity") else 1)
    parser.add_argument("--directory", default="tests")
    parser.add_argument("--shard", type=shard_of, default=(1, 1),
                        help="run every n-th test starting at the i-th, as one of n parallel CI jobs (i/n)")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    directory = str(Path(args.directory).resolve())
    modules = sorted(path.stem for path in Path(directory).glob("test*.py"))
    if not modules:
        parser.error("no test modules found")
    shard, shards = args.shard
    started = time.monotonic()
    print(f"Python: shard {shard}/{shards} of {len(modules)} modules across {args.workers} processes",
          file=sys.stderr, flush=True)
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn"),
                             max_tasks_per_child=1) as pool:
        results = list(pool.map(run_shard, [(i + 1, directory, shard, shards, i, args.workers)
                                            for i in range(args.workers)]))
    totals = {key: sum(result[key] for result in results) for key in results[0]}
    failed = not totals["ran"] or any(totals[key] for key in ("failures", "errors", "unexpected successes"))
    print(f"\nRan {totals.pop('ran')} tests in {time.monotonic() - started:.3f}s", file=sys.stderr)
    # Explicit zero counts prevent landing from picking up an earlier shard's skip summary.
    summary = ", ".join(f"{key}={value}" for key, value in totals.items())
    print(f"\n{'FAILED' if failed else 'OK'} ({summary})", file=sys.stderr, flush=True)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
