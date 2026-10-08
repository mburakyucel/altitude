#!/usr/bin/env python3
"""Scan reachable Git history and commit metadata for material unsuitable for publication.

Git-native and offline: every blob reachable from any ref is read once, path, size and content rules
run over it, and exact matches go only to the private findings file. The console prints the sanitized
summary (refs, cutoff, rules, counts by category). Nothing is validated, sent anywhere, or rewritten.

    scripts/audit_history.py --findings ~/.altitude/altitude/history-audit/full.json [--words FILE]
    scripts/audit_history.py --findings .../delta.json --since .../full.json     # only what is new

`--since` reads the ref heads recorded by a previous run and scans only objects and commits that
none of those heads reach. A private word list names things the repository must never mention, such
as other managed projects, one lower-case entry per line; keep it outside the repository.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAMESPACES = ("refs/remotes/", "refs/tags/", "refs/heads/", "refs/archive/", "refs/quarantine/", "refs/review/", "refs/stash")
PUBLISHED = ("refs/remotes/", "refs/tags/")
LARGE_BLOB = 1 << 20
LARGE_BINARY = 256 << 10
PLACEHOLDER = re.compile(r"example|placeholder|your[_-]|xxx|<[^>]*>|\.\.\.|changeme|dummy|redacted|fixture|fake|test", re.I)

#: Path rules: material that belongs to a machine or a runtime, not a repository.
PATH_RULES = {
    "key-or-credential-file": re.compile(
        r"(^|/)(\.env(\.|$)|id_(rsa|dsa|ecdsa|ed25519)|.*\.(pem|p12|pfx|jks|kdbx|key|crt)$|\.netrc|\.npmrc|\.pypirc|credentials(\.|$)|.*secret.*)", re.I),
    "runtime-state-file": re.compile(
        r"(^|/)(state/|\.altitude/|.*\.log$|.*\.jsonl$|incidents?/|tasks/[^/]+/(brief|request|events|status|progress|report)|.*transcript.*|.*session.*\.json$)", re.I),
    "build-artifact": re.compile(r"(^|/)(dist|node_modules|__pycache__|\.venv)/"),
}

#: Content rules over text blobs and commit messages. Names never spell a provider.
CONTENT_RULES = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    "cloud-access-key": re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b"),
    "chat-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "sk-prefixed-api-key": re.compile(r"\bsk-(?:[a-z]{2,6}-)?[A-Za-z0-9_-]{24,}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "url-credential": re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]{3,}@"),
    "assigned-secret": re.compile(r"(?i)\b(?:api[_-]?key|secret|passw(?:or)?d|token|auth)\w*\s*[:=]\s*['\"]?([A-Za-z0-9+/_\-=.]{16,})['\"]?"),
    "home-path": re.compile(r"(?:/home|/Users)/[a-z][a-z0-9._-]{2,}"),
    "email-address": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "private-network-address": re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b"),
    "transcript-marker": re.compile(r"\"(?:role|type)\"\s*:\s*\"(?:user|assistant)\""),
    "incident-reference": re.compile(r"\bI-\d{3}\b"),
}
#: Public addresses and placeholders that documentation and fixtures use on purpose.
ALLOWED_EMAIL = re.compile(r"@(?:example\.(?:com|org|net)|localhost|.*\.invalid|.*\.test|.*\.local)$|^noreply@|^git@github\.com$", re.I)
ALLOWED_HOME = re.compile(r"/(?:you|user|me|name|operator|username|owner|alice|bob|jane|john|acme)$", re.I)


def git(*args, **kwargs):
    return subprocess.run(["git", *args], cwd=REPO, check=True, text=True, capture_output=True, **kwargs).stdout


def entropy(value):
    counts = Counter(value)
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def refs():
    out = {}
    for line in git("for-each-ref", "--format=%(refname) %(objectname)").splitlines():
        name, sha = line.split()
        out[name] = sha
    return out


def objects(negatives):
    """Blob sha -> sorted paths, and commit shas, reachable from all refs but none of `negatives`."""
    blobs, commits = {}, []
    args = ["rev-list", "--all", "--objects", "--stdin"]
    listing = git(*args, input="".join(f"^{sha}\n" for sha in negatives))
    for line in listing.splitlines():
        sha, _, path = line.partition(" ")
        if path:
            blobs.setdefault(sha, set()).add(path)
        else:
            commits.append(sha)
    types = git("cat-file", "--batch-check=%(objectname) %(objecttype)", input="\n".join(list(blobs) + commits) + "\n")
    kinds = dict(line.split() for line in types.splitlines())
    return ({sha: sorted(paths) for sha, paths in blobs.items() if kinds.get(sha) == "blob"},
            [sha for sha in commits if kinds.get(sha) == "commit"])


def reachable(namespace, heads):
    """Object shas reachable from the refs of one namespace."""
    names = [sha for name, sha in heads.items() if name.startswith(namespace)]
    if not names:
        return set()
    listing = git("rev-list", "--objects", "--stdin", input="".join(f"{sha}\n" for sha in names))
    return {line.split(" ", 1)[0] for line in listing.splitlines()}


def read_blobs(shas):
    """Yield (sha, bytes) for every requested blob through one cat-file batch."""
    process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    for sha in shas:
        process.stdin.write(f"{sha}\n".encode())
        process.stdin.flush()
        header = process.stdout.readline().decode().split()
        size = int(header[2])
        data = process.stdout.read(size)
        process.stdout.read(1)
        yield sha, data
    process.stdin.close()
    process.stdout.close()
    process.wait()


def redact(match):
    return match[:4] + "…" + match[-2:] + f" ({len(match)} chars)" if len(match) > 12 else "…" * len(match)


def content_matches(text, words):
    """Yield (category, exact match, line number, line excerpt) for every rule hit in one text."""
    for category, rule in CONTENT_RULES.items():
        for found in rule.finditer(text):
            value = found.group(1) if found.groups() else found.group(0)
            if category == "assigned-secret" and (entropy(value) < 3.5 or PLACEHOLDER.search(found.group(0))):
                continue
            if category == "email-address" and ALLOWED_EMAIL.search(value):
                continue
            if category == "home-path" and ALLOWED_HOME.search(value):
                continue
            yield category, value, text.count("\n", 0, found.start()) + 1, line_of(text, found.start())
    lowered = text.lower()
    for word in words:
        start = lowered.find(word)
        while start >= 0:
            yield "private-word", word, text.count("\n", 0, start) + 1, line_of(text, start)
            start = lowered.find(word, start + len(word))


def line_of(text, index):
    begin = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return text[begin:end if end >= 0 else len(text)][:200]


def scan(findings_path, since, words):
    heads = refs()
    recorded = []
    if since:
        recorded = list(json.loads(Path(since).read_text())["heads"].values())
    # A head recorded by another clone, or pruned since, is not an object here; skipping it only widens the scan.
    present = git("cat-file", "--batch-check=%(objectname) %(objecttype)", input="".join(f"{sha}\n" for sha in recorded))
    negatives = [line.split()[0] for line in present.splitlines() if not line.endswith(" missing")]
    blobs, commits = objects(negatives)
    where = {namespace: reachable(namespace, heads) for namespace in NAMESPACES}

    def namespaces(sha):
        return [namespace for namespace in NAMESPACES if sha in where[namespace]]

    findings = []
    text_blobs = []
    for sha, paths in blobs.items():
        for category, rule in PATH_RULES.items():
            hit = [path for path in paths if rule.search(path)]
            if hit:
                findings.append({"kind": "path", "category": category, "blob": sha, "paths": hit, "refs": namespaces(sha)})
    for sha, data in read_blobs(list(blobs)):
        paths = blobs[sha]
        binary = b"\0" in data[:8192]
        if len(data) >= LARGE_BLOB or (binary and len(data) >= LARGE_BINARY):
            findings.append({"kind": "size", "category": "large-blob", "blob": sha, "paths": paths, "bytes": len(data),
                             "binary": binary, "refs": namespaces(sha)})
        if binary:
            continue
        text = data.decode("utf-8", errors="replace")
        text_blobs.append(sha)
        for category, value, line, excerpt in content_matches(text, words):
            findings.append({"kind": "content", "category": category, "blob": sha, "paths": paths, "line": line,
                             "match": value, "redacted": redact(value), "excerpt": excerpt, "refs": namespaces(sha)})
    authors = Counter()
    for sha in commits:
        raw = git("log", "-1", "--format=%an <%ae>%n%B", sha)
        author, _, message = raw.partition("\n")
        authors[author] += 1
        for category, value, line, excerpt in content_matches(message, words):
            findings.append({"kind": "commit", "category": category, "commit": sha, "line": line, "match": value,
                             "redacted": redact(value), "excerpt": excerpt, "refs": namespaces(sha)})
    for finding in findings:
        finding["published"] = any(namespace in finding["refs"] for namespace in PUBLISHED)

    by_category = Counter(finding["category"] for finding in findings)
    published = Counter(finding["category"] for finding in findings if finding["published"])
    distinct = {category: len({finding.get("match", finding.get("blob")) for finding in findings if finding["category"] == category})
                for category in by_category}
    summary = {
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cutoff": {"origin/main": heads.get("refs/remotes/origin/main"), "HEAD": git("rev-parse", "HEAD").strip()},
        "since": str(since) if since else None,
        "since_heads_missing": len(recorded) - len(negatives),
        "refs": len(heads),
        "refs_by_namespace": {namespace: sum(name.startswith(namespace) for name in heads) for namespace in NAMESPACES},
        "commits": len(commits),
        "blobs": len(blobs),
        "text_blobs": len(text_blobs),
        "rules": {"path": list(PATH_RULES), "content": list(CONTENT_RULES) + ["private-word"], "size": ["large-blob"]},
        "private_words": len(words),
        "author_identities": len(authors),
        "findings": {category: {"total": by_category[category], "distinct": distinct[category], "published": published[category]}
                     for category in sorted(by_category)},
        "limits": [
            "Regular expressions and a private word list; no entropy-only search, no external validation.",
            "Binary blobs get path and size checks only.",
            "Only refs present in this clone are scanned; unreferenced or reflog-only objects are not.",
        ],
    }
    private = {"summary": summary, "heads": heads, "authors": dict(authors), "findings": findings}
    findings_path.parent.mkdir(parents=True, exist_ok=True)
    findings_path.write_text(json.dumps(private, indent=1, ensure_ascii=False) + "\n")
    os.chmod(findings_path, 0o600)
    return summary


def main(argv=None):
    global REPO
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--findings", required=True, type=Path, help="private findings file to write, outside the repository")
    parser.add_argument("--since", type=Path, help="previous findings file; scan only objects none of its ref heads reach")
    parser.add_argument("--words", type=Path, help="private word list, one lower-case entry per line")
    parser.add_argument("--repo", type=Path, default=REPO, help="repository to scan (default: this one)")
    args = parser.parse_args(argv)
    REPO = args.repo.resolve()
    if REPO in args.findings.resolve().parents:
        parser.error("write the findings outside the repository")
    words = [line.strip().lower() for line in args.words.read_text().splitlines() if line.strip()] if args.words else []
    summary = scan(args.findings, args.since, words)
    json.dump(summary, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
