"""The seams and the rule layers, enforced as ratchets.

Three grep-level counts that may fall and never rise:

1. Provider names outside the engine seam. Engine-specific code belongs in `altitude/engines.py`,
   `altitude/route.py`, and `altitude/config.py`. The incident class this prevents is engine
   assumptions spreading past the seam — a dispatch, server, or CLI path that names one provider,
   until dropping a subscription or adding an engine means editing the whole system instead of three
   modules.
2. The operator's name outside a configured value. Altitude serves one operator, but the name is
   configuration; text that spells it cannot be read by anyone else and cannot be reconfigured. The
   incident class is the operator being welded into personas, code, UI, and docs.
3. Persona boundary. `personas/` is the global layer — how anyone works under Altitude on any
   project — so it must not carry this repository's own rules, which live in `AGENTS.md`.
   The incident class is a project rule leaking into every project.

A PR that reduces a count updates the baseline here in the same commit; the test says so when the
numbers disagree. The Seams section in `AGENTS.md` is the rule these numbers enforce.
"""
from __future__ import annotations

import re
import unittest

from tests.support import REPO

PROVIDER = re.compile(r"codex|claude", re.IGNORECASE)
OPERATOR = re.compile(r"burak", re.IGNORECASE)

#: Engine-specific code is expected here and nowhere else.
ENGINE_SEAM = ("altitude/config.py", "altitude/engines.py", "altitude/route.py")

#: Provider names per file in `altitude/*.py` and `bin/alt`, outside the engine seam.
PROVIDER_BASELINE = {
    "altitude/dispatch.py": 4,
    "altitude/l3.py": 32,
    "altitude/monitor.py": 6,
    "altitude/quota_codex.py": 18,
    "altitude/server.py": 6,
    "altitude/tasks.py": 3,
}

#: Occurrences of the operator's name per file, across the layers a reader meets.
OPERATOR_BASELINE = {
    "README.md": 4,
    "altitude/config.py": 1,
    "altitude/digest.py": 3,
    "altitude/dispatch.py": 4,
    "altitude/incidents.py": 2,
    "altitude/l3.py": 3,
    "altitude/land.py": 4,
    "altitude/server.py": 14,
    "altitude/tasks.py": 9,
    "bin/alt": 11,
    "docs/ARCHITECTURE.md": 4,
    "docs/CLI.md": 3,
    "docs/ROADMAP.md": 2,
    "docs/SESSION_LIFECYCLE.md": 4,
    "personas/l2.md": 1,
    "web/src/data/api.ts": 1,
    "web/src/routes/Task.test.tsx": 1,
}

#: Altitude-only rule files and rules that a global persona must never name.
PROJECT_RULES = (
    "SIMPLIFICATION.md",
    "review question",
    "deletion first",
    "parity register",
    "seam",
)


def _text(path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _counts(paths, pattern):
    counts = {}
    for path in paths:
        text = _text(path)
        if text is None:
            continue
        found = len(pattern.findall(text))
        if found:
            counts[path.relative_to(REPO).as_posix()] = found
    return counts


def _provider_files():
    files = [*sorted((REPO / "altitude").glob("*.py")), REPO / "bin" / "alt"]
    return [p for p in files if p.relative_to(REPO).as_posix() not in ENGINE_SEAM]


def _operator_files():
    files = {REPO / "bin" / "alt", REPO / "README.md", REPO / "AGENTS.md", REPO / "CLAUDE.md"}
    for root in ("personas", "altitude", "web/src", "docs"):
        files.update(p for p in (REPO / root).rglob("*") if p.is_file())
    return sorted(files)


class TestSeamRatchets(unittest.TestCase):
    def _ratchet(self, counts, baseline, what, seam):
        for name in sorted(set(counts) | set(baseline)):
            now, before = counts.get(name, 0), baseline.get(name, 0)
            if name not in baseline:
                self.fail(f"{name} is new and names {what} {now}x: {seam}")
            self.assertLessEqual(now, before, f"{name} names {what} {now}x, was {before}x: {seam}")
            if now < before:
                self.fail(f"{name} is down to {now} from {before}: update the baseline in this file")

    def test_provider_names_stay_in_the_engine_seam(self):
        self._ratchet(
            _counts(_provider_files(), PROVIDER),
            PROVIDER_BASELINE,
            "a provider",
            f"engine-specific code belongs in {', '.join(ENGINE_SEAM)}",
        )

    def test_operator_name_stays_configuration(self):
        self._ratchet(
            _counts(_operator_files(), OPERATOR),
            OPERATOR_BASELINE,
            "the operator",
            'say "the operator" or read the configured name',
        )


class TestPersonaBoundary(unittest.TestCase):
    def test_personas_carry_no_project_rule(self):
        personas = sorted((REPO / "personas").glob("*.md"))
        self.assertTrue(personas)
        for path in personas:
            text = path.read_text(encoding="utf-8").lower()
            for rule in PROJECT_RULES:
                if rule.lower() in text:
                    self.fail(
                        f"{path.name} is the global layer; "
                        f'"{rule}" is this project\'s rule and belongs in AGENTS.md'
                    )


if __name__ == "__main__":
    unittest.main()
