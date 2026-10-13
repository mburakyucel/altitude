"""Relative links in the repository's Markdown reach an existing file and, with a #fragment, an existing heading."""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP = {"node_modules", ".git", ".altitude-source", ".claude", "dist"}
FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1", re.M | re.S)
CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
ANCHOR = re.compile(r"<a\s+(?:id|name)=\"([^\"]+)\"")


def documents() -> list[Path]:
    return sorted(p for p in REPO.rglob("*.md") if not SKIP & set(p.relative_to(REPO).parts))


def anchors(path: Path) -> set[str]:
    """GitHub's heading ids: lowercase, punctuation dropped, spaces to hyphens, repeats numbered."""
    text = FENCE.sub("", path.read_text())
    seen: dict[str, int] = {}
    ids = set(ANCHOR.findall(text))
    for heading in HEADING.findall(text):
        heading = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
        slug = re.sub(r"[^\w\- ]", "", heading.replace("`", "").lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        ids.add(slug if count == 0 else f"{slug}-{count}")
    return ids


class DocLinksTest(unittest.TestCase):
    def test_relative_links_resolve(self):
        cache: dict[Path, set[str]] = {}
        broken = []
        for doc in documents():
            text = CODE.sub("", FENCE.sub("", doc.read_text()))
            for target in LINK.findall(text):
                if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I):
                    continue
                file, _, fragment = target.partition("#")
                file = file.partition("?")[0]
                path = (doc.parent / file).resolve() if file else doc
                if not path.exists():
                    broken.append(f"{doc.relative_to(REPO)}: {target} (no such file)")
                elif fragment and path.suffix == ".md":
                    if fragment not in cache.setdefault(path, anchors(path)):
                        broken.append(f"{doc.relative_to(REPO)}: {target} (no such heading)")
        self.assertEqual(broken, [])


class OrientationTest(unittest.TestCase):
    # Session analysis of 2026-10-13: the required first read included the whole architecture document, and
    # every owner saw only its first tool-output-capped slice. The required reads each fit one read, and the
    # overview links every section so an owner reads the rest by section.
    READ_LIMIT = 32_000  # bytes; about 8k tokens, under the smallest engine's tool-output cap

    def test_required_reads_fit_one_read(self):
        for name in ("AGENTS.md", "README.md", "docs/OVERVIEW.md"):
            self.assertLess(len((REPO / name).read_bytes()), self.READ_LIMIT, name)
        self.assertTrue("`docs/OVERVIEW.md`" in (REPO / "AGENTS.md").read_text(), "AGENTS.md names the overview")

    def test_overview_links_every_section(self):
        overview = REPO / "docs" / "OVERVIEW.md"
        linked = set(LINK.findall(CODE.sub("", overview.read_text())))
        missing = []
        for doc in sorted((REPO / "docs").glob("*.md")):
            if doc == overview:
                continue
            depth = 4 if doc.name == "ARCHITECTURE.md" else 2
            for level, heading in re.findall(r"^(#{2,%d})[ \t]+(.+?)[ \t]*$" % depth, FENCE.sub("", doc.read_text()), re.M):
                slug = re.sub(r"[^\w\- ]", "", heading.replace("`", "").lower()).replace(" ", "-")
                if f"{doc.name}#{slug}" not in linked:
                    missing.append(f"{doc.name}#{slug}")
        self.assertEqual(missing, [], "docs/OVERVIEW.md indexes every section")


if __name__ == "__main__":
    unittest.main()
