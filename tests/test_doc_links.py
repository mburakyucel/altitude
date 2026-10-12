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


if __name__ == "__main__":
    unittest.main()
