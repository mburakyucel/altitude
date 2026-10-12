"""Real coordinator turns while one engine's usage window is used up: its reading says so, the other engine answers."""
import time

from service_support import configure, serve
from altitude import config, engines, l3, route, state as S

RESET = (int(time.time()) // 3600 + 2) * 3600  # two hours ahead, on the hour


def main():
    configure()
    limited, other = config.ENGINES
    engines.installation = lambda engine: {"available": None, "why": "deterministic browser engine fixture"}
    route._readings = lambda: {
        limited: {"known": True, "at": time.time(), "five_hour": 100, "five_hour_resets": RESET, "seven_day": 40},
        other: {"known": False}}
    engines.claude_print = lambda _prompt, **options: {
        "session_id": "fixture-limited", "text": "", "error": "unexpected launch on the exhausted engine", "tools": []}
    answers = iter(("The release is waiting for the next quiet point.", "Nothing else is pending."))
    engines.codex_exec = lambda _prompt, **options: {
        "session_id": "fixture-other", "reported_session_id": "fixture-other", "text": next(answers),
        "tools": [], "error": None, "usage": {}}
    # One project chose the limited engine for L3 and answers on the other; one routes to the limited engine alone.
    for project, routing in (("atlas", None), ("harbor", f"{limited}:opus")):
        repo = config.PROJECT_ROOTS[0] / project
        repo.mkdir(parents=True)
        with config.add_project(project, path=repo):
            pass
        projects = config.load_projects()
        projects[project]["l3_choice"] = {"engine": limited}
        if routing:
            projects[project]["routing"] = config.parse_routing(routing)
        config.save_projects(projects)
        S.regen_state_md(project)
        l3.chat_log(project, "user", "Keep the release notes short.", trigger="chat", turn_id=f"{project}-earlier")
        l3.chat_log(project, "assistant", "The release notes stay short.", trigger="chat", turn_id=f"{project}-earlier",
                    engine=limited)
        S.write_json(l3.info_path(project), {"engine_last": limited})
    serve()


if __name__ == "__main__":
    main()
