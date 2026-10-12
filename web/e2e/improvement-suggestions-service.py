"""Improvement suggestions posted into a fictional project's chat through the real run lifecycle: a scripted
reviewer at the engine seam, L3's post and Altitude's message, then a run with nothing to suggest and a failed one."""
from datetime import datetime, timedelta, timezone

from service_support import configure, serve
from altitude import config, engines, improve, improve_eval, l3, route, server

NOW = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)


def finished(n: int, title: str, ended: datetime) -> dict:
    at = lambda hours: (ended + timedelta(hours=hours - 6)).isoformat()
    return {"slug": f"dock-{n}", "title": title, "state": "done", "created": at(0), "reported": at(5),
            "finished": at(6), "verdict": "delivered", "tokens": 1000,
            "messages": [{"id": f"m{n}", "at": at(5.5), "role": "operator", "text": "Asked and answered again."}]}


def suggestion(target: str, title: str, explanation: str) -> dict:
    return {"target": target, "title": title, "explanation": explanation, "where": "the owner instructions",
            "signal": "after_report", "anchors": ["CITE"], "uncertainty": "A small sample."}


def main():
    configure()
    # Two batches of finished work: the first is read by a failed review and one with nothing to suggest, the second,
    # finished after those, by the review whose suggestions are posted.
    first = ["Persist dock paths", "Export the harbor list", "Fix the tide timer", "Rename crane slots", "Retry berth sync"]
    second = ["Trim the quay log", "Pin the buoy schema", "Merge the pier docs", "Fix the lock gauge", "Sort the cargo tags"]
    improve_eval.load({"project": "alpha", "now": NOW.isoformat(), "tasks":
                       [finished(n, title, NOW - timedelta(days=n)) for n, title in enumerate(first, 1)]
                       + [finished(n, title, NOW - timedelta(minutes=150 - 10 * n)) for n, title in enumerate(second, 6)]})
    repo = config.PROJECT_ROOTS[0] / "alpha"
    repo.mkdir(parents=True)
    with config.add_project("alpha", path=repo):
        pass
    l3.save_info("alpha", {"session_id": "fictional-session", "engine_last": config.ENGINES[0], "turns": 1})
    # The run lifecycle is real; the Altitude-project gate and Claude's reserve are unit-tested and fixed here.
    improve._altitude = lambda _project: True
    route.improvement_reserve = lambda: None
    answers = [{"suggestions": [], "error": "it went over its usage limit"}, {"suggestions": []}, {"suggestions": [
        suggestion("agents", "Owners re-ask merge questions you already answered",
                   "Owners ask again after an answer in task chat. A rule to cite the earlier answer would stop it."),
        suggestion("project", "The harbor list is rebuilt by hand after each export",
                   "Each export task ends with a manual rebuild. A make target would remove the step."),
        suggestion("agents", "Reports name checks that never ran",
                   "Two reports listed a check as passed without its log.")]}]

    def review(text, instructions, schema, *, model, timeout):
        aliases = [a for group in improve.ALIASES.findall(text) for a in group.split(", ")][:3]
        answer = answers.pop(0)
        for s in answer.get("suggestions", []):
            s["anchors"] = aliases
        return {"model": model, "engine_model": model, "usage": {}, "cost": 0.0, "tokens": 900,
                "over_limit": False, "answer": answer, "error": answer.pop("error", None)}
    engines.improvement_review = review

    def run(message: str, at: datetime, reply: str) -> str:
        turn = f"{abs(hash(message)) % 16**12:012x}"
        l3.chat_log("alpha", "user", message, trigger="chat", turn_id=turn, at=at.isoformat())
        l3.chat_log("alpha", "assistant", reply, trigger="chat", turn_id=turn, at=(at + timedelta(seconds=20)).isoformat())
        receipt = improve.start("alpha", turn, now=at)
        improve.execute("alpha", receipt["run"])
        return receipt["run"]

    run("Get improvement suggestions, please.", NOW - timedelta(hours=4), "Started a review of the last two weeks.")
    run("Try again.", NOW - timedelta(hours=3), "Started another review.")
    posted = run("Any suggestions from today's work?", NOW - timedelta(minutes=30), "Started a review of today's work.")
    improve.post("alpha", posted, [1, 3], [(2, "an open task already adds that target")], now=NOW - timedelta(minutes=20))
    serve()


if __name__ == "__main__":
    main()
