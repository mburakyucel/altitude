"""The Weekly improvement review as a built-in agent of fictional projects, through the real handlers and records:
"altitude" has six finished tasks and no agreement yet, so the review waits under Built in; "beacon" has it on, three
tasks, and this week's Monday run skipped; "cove" had it on until its terms changed, which paused it and said so in
its chat. The reviewer waits until the service stops, so a started review stays reading."""
from datetime import datetime, timedelta, timezone
import threading

from service_support import configure, serve
from altitude import config, engines, improve, improve_eval, l3, route, schedule, state as S

NOW = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)


def finished(n: int, title: str, ended: datetime) -> dict:
    at = lambda hours: (ended + timedelta(hours=hours - 6)).isoformat()
    return {"slug": f"dock-{n}", "title": title, "state": "done", "created": at(0), "reported": at(5),
            "finished": at(6), "verdict": "delivered", "tokens": 1000,
            "messages": [{"id": f"m{n}", "at": at(5.5), "role": "operator", "text": "Asked and answered again."}]}


def project(name: str, titles: list[str], end: datetime = NOW) -> None:
    improve_eval.load({"project": name, "now": NOW.isoformat(), "tasks": [
        finished(n + 1, title, end - timedelta(hours=2 + n)) for n, title in enumerate(titles)]})
    repo = config.PROJECT_ROOTS[0] / name
    repo.mkdir(parents=True)
    with config.add_project(name, path=repo):
        pass
    l3.save_info(name, {"session_id": f"fictional-{name}", "engine_last": config.ENGINES[0], "turns": 1})


def main():
    configure()
    # The Altitude-project gate and the engine's reserve are unit-tested and fixed here.
    improve._altitude = lambda _project: True
    route.improvement_reserve = lambda: None
    stopping = threading.Event()

    def review(text, instructions, schema, *, model, timeout):
        stopping.wait()
        return {"model": model, "engine_model": model, "usage": {}, "cost": 0.0, "tokens": 0, "over_limit": False,
                "answer": {"suggestions": []}, "error": None}
    engines.improvement_review = review

    project("altitude", ["Persist dock paths", "Export the harbor list", "Fix the tide timer", "Rename crane slots",
                         "Retry berth sync", "Trim the quay log"])
    project("cove", ["Index the mooring list", "Fix the beacon clock", "Split the wharf notes", "Rename the tug roster",
                     "Check the ferry times", "Sort the cargo tags"])
    # beacon: agreed and switched on before this week's Monday, whose run found too few finished tasks.
    monday = schedule.latest(improve.WEEKLY_DAYS, improve.WEEKLY_TIME, NOW)
    project("beacon", ["Pin the buoy schema", "Merge the pier docs", "Fix the lock gauge"], monday)
    version = improve.terms("beacon")["version"]
    improve.set_weekly("beacon", True, agree=version, now=monday - timedelta(days=1))
    improve.weekly("beacon", now=monday + timedelta(minutes=1))
    # cove: switched on under earlier terms; the next check pauses it and says so in chat.
    improve.set_weekly("cove", True, agree=version, now=NOW - timedelta(days=3))
    with S.project_lock("cove"):
        record = improve._load("cove")
        record["agreement"]["terms"] = "0123456789ab"
        S.write_json(improve._path("cove"), record)
    improve.reconcile("cove")
    serve(release=stopping.set)


if __name__ == "__main__":
    main()
