"""The improvement review's eval: fictional scenarios loaded as ordinary records, matched against their keys.

A scenario becomes the same task folders and project chat a real project has, so the eval runs the real
prompt builder and reviewer. Nothing here reads the operator's own runtime records. `run` calls the reviewer
on the plan and stops before its usage ceiling; `score` combines the code's matching with a blind grader's
rubric into the results table. It runs by hand only (docs/DEVELOPMENT.md#improvement-review-eval).
"""
from __future__ import annotations

import json
import random
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import config, engines, improve, state as S

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "improvement"
HALVES = ("develop", "heldback")
EVENT_KINDS = {"merge-approval-refused", "grant-refused", "ci-recheck", "delivery", "resume-failed",
               "dispatch-failed", "report-superseded", "escalated", "fault", "fyi"}
ROLES = {"operator": config.OPERATOR_ACTOR, "l2": "l2", "l3": "l3", "system": "system"}


def scenarios(half: str) -> list[tuple[str, dict, dict]]:
    """(name, scenario, key) for every scenario of one half, in name order."""
    if half not in HALVES:
        raise ValueError(f"unknown half: {half}")
    out = []
    for path in sorted((FIXTURES / half).glob("*.json")):
        if path.name.endswith(".key.json"):
            continue
        key = path.with_name(path.stem + ".key.json")
        out.append((path.stem, json.loads(path.read_text()), json.loads(key.read_text())))
    return out


def _line(path: Path, row: dict) -> int:
    """Append one JSONL row; returns its byte offset, the anchor a chat row is cited by."""
    path.parent.mkdir(parents=True, exist_ok=True)
    offset = path.stat().st_size if path.exists() else 0
    with path.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return offset


def _questions(slug: str, entries: list[dict], messages: dict[str, str]) -> list[dict]:
    out = []
    for q in entries:
        resolution = q.get("resolution")
        if resolution:
            by = ROLES[resolution["by"]]
            resolution = {"disposition": resolution["disposition"], "at": resolution["at"], "by": by,
                          "message_id": resolution.get("message"), "text": "",
                          "source": "task" if resolution.get("message") else None}
        anchor = f"{q['id']}-{q['revision']}"
        out.append({"id": q["id"], "revision": q["revision"], "anchor_id": anchor,
                    "status": "resolved" if resolution else "open", "audience": q.get("audience", "operator"),
                    "question": q["text"], "asked": q["asked"], "since": q["asked"], "asked_by": "l2",
                    "kind": "asks", "resolution": resolution,
                    **({"asked_again": True} if q.get("asked_again") else {}),
                    "message": {"id": anchor, "at": q["asked"], "role": "l2", "by": "l2", "text": q["text"],
                                "question_id": q["id"], "question_revision": q["revision"]}})
    return out


def _events(task: dict) -> list[dict]:
    """The task's events in time order: the scenario's own plus the lifecycle Altitude would have written."""
    rows = [(task["created"], {"kind": "new", "by": "l3"}),
            (task["created"], {"kind": "state", "frm": "queued", "to": "running", "by": "l3"})]
    for review in task.get("reviews", []):
        rows.append((review["requested"], {"kind": "review-requested", "review_id": review["id"], "by": "l2"}))
    for event in task.get("events", []):
        kind = event["kind"]
        if kind not in EVENT_KINDS:
            raise ValueError(f"{task['slug']}: unknown event kind {kind}")
        data = {k: v for k, v in event.items() if k not in ("id", "at", "kind")}
        if kind == "fault":
            data = {"kind": "state", "frm": "running", "to": "blocked", "by": "altitude",
                    "reason": f"system fault [{event['fault']}]: {event.get('reason', '')}".rstrip(": ")}
        elif kind == "report-superseded":
            # Altitude records the replaced report, not a reason; `unmerged` names the PRs it reported unmerged.
            data = {"kind": kind, "report": {"landed": {"prs": [{"number": n, "merged": False}
                                                               for n in event.get("unmerged", [])]}}}
        else:
            data = {"kind": kind, **data}
        rows.append((event["at"], {**data, "_id": event["id"]}))
    if task.get("reported"):
        rows.append((task["reported"], {"kind": "state", "frm": "running", "to": "reported", "by": "l2",
                                        "verdict": task.get("verdict", "")}))
    final = task["state"]
    rows.append((task["finished"], {"kind": "state", "frm": "reported" if task.get("reported") else "running",
                                    "to": final, "by": "l3"}))
    rows.sort(key=lambda row: row[0])
    return [{"at": at, **data} for at, data in rows]


def load(scenario: dict) -> dict[str, str]:
    """Write one scenario's records under its project; returns scenario anchors -> canonical anchors."""
    project = scenario["project"]
    base = config.project_dir(project)
    if base.exists():
        raise ValueError(f"{project}: records already exist")
    anchors = {}
    for task in scenario["tasks"]:
        slug = task["slug"]
        if task["state"] not in ("done", "rejected"):
            raise ValueError(f"{slug}: a finished task is done or rejected")
        d = S.archive_dir(project) / slug
        messages = {}
        for m in task.get("messages", []):
            role = ROLES[m["role"]]
            _line(d / "conversation.jsonl", {"id": m["id"], "at": m["at"], "role": role, "by": role, "text": m["text"]})
            anchors[f"{slug}:{m['id']}"] = f"{slug}/m/{m['id']}"
            messages[m["id"]] = m["at"]
        questions = _questions(slug, task.get("questions", []), messages)
        for q in questions:
            anchors[f"{slug}:{q['id']}@{q['revision']}"] = f"{slug}/q/{q['id']}/{q['revision']}"
        reviews = []
        for review in task.get("reviews", []):
            reviews.append({"id": review["id"], "subject": review["subject"], "state": "completed",
                            "requested_at": review["requested"], "requested_by": "l2",
                            "result": {"text": "", "findings": review.get("findings", []), "limitations": []},
                            "dispositions": review.get("dispositions", []),
                            "message": {"id": review["id"], "at": review["requested"], "role": "system", "by": "l2",
                                        "review_id": review["id"],
                                        "text": f"Adversarial {review['subject']} review requested."}})
            for finding in review.get("findings", []):
                anchors[f"{slug}:{review['id']}/{finding['id']}"] = f"{slug}/r/{review['id']}/{finding['id']}"
        for index, event in enumerate(_events(task)):
            if "_id" in event:
                anchors[f"{slug}:{event.pop('_id')}"] = f"{slug}/e/{index}"
            _line(d / "events.log", event)
        S.write_json(d / "status.json", {
            "slug": slug, "title": task["title"], "state": task["state"], "created": task["created"],
            "attempt": 1, "prs": [], "source": "chat", "questions": questions, "reviews": reviews,
            "token_usage": {"status": "observed", "total_tokens": task.get("tokens", 0)}})
        for i, _ in enumerate(task.get("report_review") or []):
            anchors[f"{slug}:report/{i}"] = f"{slug}/report/{i}"
        if task.get("report_review") is not None:
            S.write_json(d / "report.json", {"landed": {"prs": []}, "review": task["report_review"], "blocked": None})
    for task in scenario.get("current", []):
        S.write_json(S.tasks_dir(project) / task["slug"] / "status.json",
                     {"slug": task["slug"], "title": task["title"], "state": task["state"],
                      "created": scenario["now"], "attempt": 1, "prs": [], "questions": [], "reviews": []})
    if scenario.get("earlier"):
        S.write_json(base / "improvements.json", {"runs": [{"suggestions": [
            {"title": e["title"], "state": e["outcome"]} for e in scenario["earlier"]]}]})
    for row in scenario.get("chat", []):
        operator = row["role"] == "operator"
        offset = _line(base / "chat.jsonl", {"at": row["at"], "role": "user" if operator else "assistant",
                                              "text": row["text"], "turn_id": row["turn"],
                                              **({"trigger": "chat"} if operator else {})})
        anchors[f"chat:{row['id']}"] = f"chat/{offset}"
    return anchors


def check(half: str) -> list[str]:
    """Problems with one half's scenarios and keys; loads each under this process's Altitude home."""
    problems = []
    names = [name for name, _, _ in scenarios(half)]
    for name, scenario, key in scenarios(half):
        try:
            anchors = load(scenario)
        except (KeyError, ValueError) as exc:
            problems.append(f"{name}: {exc!r}")
            continue
        if key.get("expect") not in ("suggestions", "nothing"):
            problems.append(f"{name}: expect is suggestions or nothing")
        if (key["expect"] == "nothing") != (not key.get("patterns")):
            problems.append(f"{name}: only a scenario expecting nothing has no patterns")
        for item in key.get("patterns", []) + key.get("decoys", []):
            missing = [a for a in item["anchors"] if a not in anchors]
            if missing:
                problems.append(f"{name} {item['id']}: unknown anchors {missing}")
        for pattern in key.get("patterns", []):
            if len(pattern["anchors"]) < 3:
                problems.append(f"{name} {pattern['id']}: fewer than three anchors")
            if pattern["kind"] not in ("counted", "words", "shared") or pattern["target"] not in ("project", "agents"):
                problems.append(f"{name} {pattern['id']}: kind or target")
            if pattern["kind"] == "shared" and not pattern.get("wrong_explanation"):
                problems.append(f"{name} {pattern['id']}: shared cause without wrong_explanation")
    if len(set(s["project"] for _, s, _ in scenarios(half))) != len(names):
        problems.append("two scenarios share a project name")
    return problems


#: Short names ("sonnet") for the models the engine seam offers the review, cheapest plan use first.
MODELS = {config.model_family(model): model for model in sorted(config.IMPROVEMENT_MODELS,
                                                                  key=lambda m: config.IMPROVEMENT_MODELS[m])}
#: The approved bar: per run, counted and shared-cause patterns caught; words-only caught; across runs, wrong
#: suggestions outside the controls; invalid answers.
BAR = {"counted": 0.8, "words": 0.5, "wrong": 1 / 6, "invalid": 1 / 15}
OUTCOMES = ("caught", "wrong", "sound")


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value)


def prepare(half: str) -> list[dict]:
    """Every scenario of a half loaded and its prompt built, with the key's anchors made canonical."""
    out = []
    for name, scenario, key in scenarios(half):
        anchors = load(scenario)
        built = improve.prompt(scenario["project"], end=_date(scenario["now"]))
        shown = set(built["anchors"].values())
        patterns = [{**p, "places": {anchors[a] for a in p["anchors"]}} for p in key.get("patterns", [])]
        for p in patterns:
            p["reached"] = len(p["places"] & shown) >= 2
            p["counts"] = len(p["places"] & _signal_places(scenario["project"], built)) >= 2
        decoys = [{**d, "places": {anchors[a] for a in d["anchors"]}} for d in key.get("decoys", [])]
        out.append({"name": name, "scenario": scenario, "key": key, "built": built, "patterns": patterns,
                    "decoys": decoys, "names": {v: k for k, v in anchors.items()}})
    return out


def _signal_places(project: str, built: dict) -> set[str]:
    """Places a counts-only report would point at: those that produced a signal in the reviewed window."""
    items, _ = improve.finished(project, _date(built["end"]), _date(built["start"]))
    return {a for item in items for anchors in improve.signals(project, item).values() for a in anchors}


def match(suggestions: list[dict], patterns: list[dict]) -> dict[int, str]:
    """Suggestion number -> pattern id, one to one, by most shared planted places (at least two)."""
    pairs = sorted(((len(set(s["anchors"]) & p["places"]), s["n"], p["id"]) for s in suggestions for p in patterns),
                   reverse=True)
    taken, used, out = set(), set(), {}
    for shared, n, pattern in pairs:
        if shared >= 2 and n not in taken and pattern not in used:
            out[n] = pattern
            taken.add(n)
            used.add(pattern)
    return out


def run(half: str, models: list[str], repeats: int, ceiling: float, out: Path, only: list[str] | None = None) -> dict:
    """Call the reviewer for every scenario, model and repeat, stopping before the yardstick passes the ceiling."""
    prepared = [p for p in prepare(half) if not only or p["name"] in only]
    out.mkdir(parents=True, exist_ok=True)
    calls, spent, stopped = out / "calls.jsonl", 0.0, None
    for repeat in range(1, repeats + 1):
        for short in models:
            model = MODELS[short]
            for p in prepared:
                if spent + engines.improvement_budget(model) > ceiling:
                    stopped = f"stopped before {short} run {repeat} on {p['name']}: the next call could pass the ceiling"
                    break
                result = improve.review(p["built"], model=model)
                cost = result["call"].get("cost")
                spent += cost if cost is not None else engines.improvement_budget(model)
                row = {"half": half, "scenario": p["name"], "model": short, "repeat": repeat, **result,
                       "matched": match(result["suggestions"], p["patterns"])}
                with calls.open("a") as f:
                    f.write(json.dumps(row) + "\n")
                print(f"{short} run {repeat} {p['name']}: {len(result['suggestions'])} kept, "
                      f"{len(result['dropped'])} dropped, {result['call'].get('error') or 'ok'}", flush=True)
            if stopped:
                break
        if stopped:
            break
    summary = {"half": half, "models": models, "repeats": repeats, "ceiling": ceiling, "spent": round(spent, 4),
               "complete": stopped is None, "stopped": stopped}
    S.write_json(out / "run.json", summary)
    grading_sheet(prepared, out)
    return summary


def _place(scenario: dict, name: str) -> str:
    """The text a cited scenario place shows, for the grader."""
    if name.startswith("chat:"):
        row = next(r for r in scenario.get("chat", []) if r["id"] == name[5:])
        return f"project chat, {row['role']}: {row['text']}"
    slug, ref = name.split(":", 1)
    task = next(t for t in scenario["tasks"] if t["slug"] == slug)
    if ref.startswith("report/"):
        return f"{slug} report: {task['report_review'][int(ref[7:])]}"
    if "@" in ref:
        qid, rev = ref.split("@")
        q = next(q for q in task["questions"] if q["id"] == qid and str(q["revision"]) == rev)
        return f"{slug} question rev {rev}: {q['text']}"
    if "/" in ref:
        rid, fid = ref.split("/")
        review = next(r for r in task["reviews"] if r["id"] == rid)
        return f"{slug} review finding: {next(f for f in review['findings'] if f['id'] == fid)}"
    for m in task.get("messages", []):
        if m["id"] == ref:
            return f"{slug} message, {m['role']}: {m['text']}"
    event = next(e for e in task.get("events", []) if e["id"] == ref)
    return f"{slug} event: {event}"


def grading_sheet(prepared: list[dict], out: Path) -> None:
    """Every kept suggestion, shuffled and without its model, beside its scenario's key, for a blind grader."""
    by_name = {p["name"]: p for p in prepared}
    items, keyed = [], {}
    for line in (out / "calls.jsonl").read_text().splitlines():
        row = json.loads(line)
        p = by_name[row["scenario"]]
        for s in row["suggestions"]:
            items.append({"scenario": row["scenario"], "suggestion": {k: s[k] for k in (
                "target", "title", "explanation", "where", "signal", "uncertainty")},
                "cited": [_place(p["scenario"], p["names"][a]) for a in s["anchors"] if a in p["names"]]
                + [f"(unplanted place {a})" for a in s["anchors"] if a not in p["names"]],
                "_call": [row["model"], row["repeat"], s["n"]]})
    random.Random(len(items)).shuffle(items)
    sheet = []
    for i, item in enumerate(items, 1):
        keyed[f"G{i}"] = {"scenario": item["scenario"], "call": item.pop("_call")}
        sheet.append({"item": f"G{i}", **item})
    keys = {name: {"expect": p["key"]["expect"],
                   "patterns": [{k: v for k, v in pattern.items() if k in ("id", "kind", "target", "problem", "fix",
                                                                          "wrong_explanation")}
                                for pattern in p["key"].get("patterns", [])],
                   "decoys": [{"id": d["id"], "type": d["type"], "why": d["why"]} for d in p["key"].get("decoys", [])]}
            for name, p in by_name.items()}
    S.write_json(out / "grading.json", {"rubric": GRADING, "keys": keys, "items": sheet})
    S.write_json(out / "grading-key.json", keyed)


GRADING = """Grade each item on its own; you do not know which model wrote it. For the item's scenario, decide which
planted pattern it addresses, if any, then answer: supported (the cited places show what it claims), cause (it
names the pattern's cause, not its wrong_explanation), target (project or agents as the pattern's), removes
(its change would remove the friction). Outcome: caught when it addresses a pattern and all four hold; wrong when
it flags a decoy, a clean or decoys-only project's ordinary work, a wrong cause or target, or a change that would
not help; sound when it addresses no planted pattern but is supported and would help. Write grades.json:
{"G1": {"pattern": "P1" or null, "supported": true, "cause": true, "target": true, "removes": true,
"outcome": "caught" | "wrong" | "sound", "why": "one sentence"}, ...}"""


def score(half: str, out: Path) -> dict:
    """The results table: per model, each run's catch rates and the bar, from the matcher and the blind grades."""
    prepared = {p["name"]: p for p in prepare(half)}
    summary = S.read_json(out / "run.json")
    keyed, grades = S.read_json(out / "grading-key.json"), S.read_json(out / "grades.json")
    graded = {(k["scenario"], *k["call"]): grades[item] for item, k in keyed.items()}
    rows = [json.loads(line) for line in (out / "calls.jsonl").read_text().splitlines()]
    table = {}
    for short in summary["models"]:
        mine = [r for r in rows if r["model"] == short]
        runs, wrong_controls, wrong, kept, invalid, tokens = [], 0, 0, 0, 0, []
        for repeat in sorted({r["repeat"] for r in mine}):
            caught, reached = Counter(), Counter()
            for r in (r for r in mine if r["repeat"] == repeat):
                p = prepared[r["scenario"]]
                for pattern in p["patterns"]:
                    if pattern["reached"]:
                        group = "words" if pattern["kind"] == "words" else "counted"
                        reached[group] += 1
                        n = next((int(n) for n, pid in r["matched"].items() if pid == pattern["id"]), None)
                        grade = graded.get((r["scenario"], short, repeat, n)) if n else None
                        if grade and grade["outcome"] == "caught" and grade.get("pattern") == pattern["id"]:
                            caught[group] += 1
            runs.append({group: caught[group] / reached[group] if reached[group] else None for group in ("counted", "words")})
        for r in mine:
            invalid += bool(r["call"].get("error") or r["dropped"])
            tokens.append(r["call"].get("tokens") or 0)
            for s in r["suggestions"]:
                outcome = graded[(r["scenario"], short, r["repeat"], s["n"])]["outcome"]
                if prepared[r["scenario"]]["key"]["expect"] == "nothing":
                    wrong_controls += outcome != "sound"
                else:
                    kept += 1
                    wrong += outcome == "wrong"
        passes = (summary["complete"] and runs
                  and all((run["counted"] is None or run["counted"] >= BAR["counted"])
                          and (run["words"] is None or run["words"] >= BAR["words"]) for run in runs)
                  and wrong_controls == 0 and (not kept or wrong / kept <= BAR["wrong"])
                  and invalid / max(1, len(mine)) <= BAR["invalid"])
        table[short] = {"runs": runs, "wrong_on_controls": wrong_controls, "wrong_elsewhere": f"{wrong}/{kept}",
                        "invalid": f"{invalid}/{len(mine)}", "mean_tokens": round(sum(tokens) / max(1, len(tokens))),
                        "max_tokens": max(tokens, default=0), "passes": bool(passes)}
    patterns = [pattern for p in prepared.values() for pattern in p["patterns"]]
    passing = [m for m in MODELS if table.get(m, {}).get("passes")]
    result = {"half": half, "complete": summary["complete"], "stopped": summary["stopped"],
              "yardstick_spent": summary["spent"], "ceiling": summary["ceiling"], "models": table,
              "counts_alone": f"{sum(p['counts'] for p in patterns)}/{len(patterns)}",
              "reached": f"{sum(p['reached'] for p in patterns)}/{len(patterns)}",
              "recommended": passing[0] if passing else None}
    S.write_json(out / "score.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python3 -m altitude.improve_eval")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check").add_argument("half", choices=HALVES)
    show = sub.add_parser("show", help="print a develop scenario's prompt")
    show.add_argument("name")
    go = sub.add_parser("run", help="call the reviewer on the plan; stops before the ceiling")
    go.add_argument("half", choices=HALVES)
    go.add_argument("--models", default=",".join(MODELS))
    go.add_argument("--repeats", type=int, default=3)
    go.add_argument("--ceiling", type=float, required=True, help="usage ceiling in the engine's reported-cost yardstick")
    go.add_argument("--only", help="comma-separated scenario names")
    go.add_argument("--out", type=Path, required=True)
    graded = sub.add_parser("score", help="combine the matcher and grades.json into the results table")
    graded.add_argument("half", choices=HALVES)
    graded.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if config.ROOT.resolve() == (config.HOME / ".altitude").resolve():
        parser.error("set ALTITUDE_HOME to a throwaway directory: the eval writes fictional records")
    if args.command == "check":
        problems = check(args.half)
        print("\n".join(problems) or f"{args.half}: {len(scenarios(args.half))} scenarios load")
        return 1 if problems else 0
    if args.command == "show":
        p = next((p for p in prepare("develop") if p["name"] == args.name), None)
        if p is None:
            parser.error("show reads develop scenarios only")
        print(p["built"]["text"])
        return 0
    if args.command == "run":
        models = args.models.split(",")
        if any(m not in MODELS for m in models):
            parser.error(f"models are {', '.join(MODELS)}")
        print(json.dumps(run(args.half, models, args.repeats, args.ceiling, args.out,
                             args.only.split(",") if args.only else None), indent=2))
        return 0
    print(json.dumps(score(args.half, args.out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
