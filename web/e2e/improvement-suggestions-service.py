"""Improvement suggestions in a fictional project through the real run lifecycle: a scripted reviewer at the engine
seam, L3's post and the operator's presses as Altitude records them. A review eight days back has a task made, one
suggestion not decided and one accepted whose task turn ended without a task; today's reviews failed, found nothing
and posted two undecided suggestions; a last review is still reading."""
from datetime import datetime, timedelta, timezone
import uuid

from service_support import configure, serve
from altitude import config, engines, improve, improve_eval, l3, route, server
from altitude import state as S, tasks as T

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
    # Four batches of finished work, each read by the reviews that follow it.
    batches = [
        (["Persist dock paths", "Export the harbor list", "Fix the tide timer", "Rename crane slots", "Retry berth sync"],
         lambda n: NOW - timedelta(days=9 + n)),
        (["Trim the quay log", "Pin the buoy schema", "Merge the pier docs", "Fix the lock gauge", "Sort the cargo tags"],
         lambda n: NOW - timedelta(days=1 + n)),
        (["Index the mooring list", "Fix the beacon clock", "Split the wharf notes", "Rename the tug roster",
          "Check the ferry times"], lambda n: NOW - timedelta(minutes=150 - 10 * n)),
        (["Trim the anchor log", "Pin the dock map", "Merge the crane notes", "Fix the berth labels", "Sort the tide table"],
         lambda n: NOW - timedelta(minutes=25 - 4 * n)),
    ]
    improve_eval.load({"project": "alpha", "now": NOW.isoformat(), "tasks": [
        finished(5 * b + n + 1, title, when(n)) for b, (titles, when) in enumerate(batches) for n, title in enumerate(titles)]})
    repo = config.PROJECT_ROOTS[0] / "alpha"
    repo.mkdir(parents=True)
    with config.add_project("alpha", path=repo):
        pass
    l3.save_info("alpha", {"session_id": "fictional-session", "engine_last": config.ENGINES[0], "turns": 1})
    # The run lifecycle is real; the Altitude-project gate and Claude's reserve are unit-tested and fixed here.
    improve._altitude = lambda _project: True
    route.improvement_reserve = lambda: None
    # The operator agreed to the terms and turned Weekly on in settings before these reviews.
    improve.set_weekly("alpha", True, agree=improve.terms("alpha")["version"], now=NOW - timedelta(days=9))
    # L3 never runs: a queued task turn stays pending, so a Make a task press reads "L3 is opening a task".
    server.request_l3_drain = lambda _project: False
    answers = [
        {"suggestions": [
            suggestion("agents", "Owners restate the brief in every report",
                       "Each report opens with the brief again. Starting from the outcome would shorten them."),
            suggestion("project", "Crane slots are renamed by hand",
                       "Two tasks renamed slots one by one. A rename script would do it in one step."),
            suggestion("agents", "Reviews wait for a reviewer that is busy",
                       "Owners started reviews while another held the reviewer and waited without saying so.")]},
        {"suggestions": [], "error": "it went over its usage limit"},
        {"suggestions": []},
        {"suggestions": [
            suggestion("agents", "Owners re-ask merge questions you already answered",
                       "Owners ask again after an answer in task chat. A rule to cite the earlier answer would stop it."),
            suggestion("project", "The harbor list is rebuilt by hand after each export",
                       "Each export task ends with a manual rebuild. A make target would remove the step."),
            suggestion("agents", "Reports name checks that never ran",
                       "Two reports listed a check as passed without its log.")]}]

    def review(text, instructions, schema, *, model, timeout):
        aliases = [a for group in improve.ALIASES.findall(text) for a in group.split(", ")]
        answer = answers.pop(0)
        for s in answer.get("suggestions", []):
            s["anchors"] = aliases
        return {"model": model, "engine_model": model, "usage": {}, "cost": 0.0, "tokens": 900,
                "over_limit": False, "answer": answer, "error": answer.pop("error", None)}
    engines.improvement_review = review

    real_now = S.now

    def clock(at: datetime) -> None:
        """Records written while seeding carry the seeded time, as they would have when it happened."""
        S.now = lambda: at.isoformat()

    def run(message: str, at: datetime, reply: str, *, read: bool = True) -> str:
        clock(at + timedelta(minutes=2))
        turn = uuid.uuid4().hex[:12]
        l3.chat_log("alpha", "user", message, trigger="chat", turn_id=turn, at=at.isoformat())
        l3.chat_log("alpha", "assistant", reply, trigger="chat", turn_id=turn, at=(at + timedelta(seconds=20)).isoformat())
        receipt = improve.start("alpha", turn, now=at)
        if read:
            improve.execute("alpha", receipt["run"])
        return receipt["run"]

    def press(ref: str, action: str, at: datetime) -> dict:
        clock(at)
        improve.decide("alpha", ref, action, uuid.uuid4().hex, now=at)
        run_id, n = ref.split("/")
        return next(d for d in improve._find(improve._load("alpha"), run_id)["decisions"] if d["n"] == int(n))

    earlier = run("Any suggestions from last week?", NOW - timedelta(days=8), "Started a review of the last two weeks.")
    clock(NOW - timedelta(days=8) + timedelta(minutes=10))
    improve.post("alpha", earlier, [1, 2, 3], [], now=NOW - timedelta(days=8) + timedelta(minutes=10))
    pressed = NOW - timedelta(days=8) + timedelta(hours=1)
    # Make a task on 1: L3's turn ran and opened its task. On 3 the turn ended without a task.
    made = press(f"{earlier}/1", "accept", pressed)
    T.new("alpha", "Start reports from the outcome", "Brief.", improvement=f"{earlier}/1")
    tried = press(f"{earlier}/3", "accept", pressed)
    with improve.S.project_lock("alpha"):
        l3.unqueue_locked("alpha", made["turn"])
        l3.unqueue_locked("alpha", tried["turn"])

    run("Get improvement suggestions, please.", NOW - timedelta(hours=4), "Started a review of the last week.")
    run("Try again.", NOW - timedelta(hours=3), "Started another review.")
    posted = run("Any suggestions from today's work?", NOW - timedelta(minutes=30), "Started a review of today's work.")
    clock(NOW - timedelta(minutes=20))
    improve.post("alpha", posted, [1, 3], [(2, "an open task already adds that target")], now=NOW - timedelta(minutes=20))
    run("And the last few?", NOW, "Started a review of the last half hour.", read=False)
    S.now = real_now
    serve()


if __name__ == "__main__":
    main()
