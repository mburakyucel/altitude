"""Real coordinator turns while one engine's sign-in has lapsed: the provider fixtures refuse it as the CLI does."""
import json

from service_support import configure, serve
from altitude import config, engines, l3, state as S

REVOKED = "Failed to authenticate: OAuth token revoked. Please log in again or contact your administrator."
STREAM = "".join(json.dumps(event) + "\n" for event in (
    {"type": "system", "subtype": "init", "session_id": "fixture-lapse"},
    {"type": "assistant", "error": "authentication_failed", "is_api_error_message": True,
     "message": {"model": "<synthetic>", "content": [{"type": "text", "text": REVOKED}]}},
    {"type": "result", "is_error": True, "api_error_status": 401, "result": REVOKED}))


def main():
    configure()
    lapsed, other = config.ENGINES
    engines.installation = lambda engine: {"available": None, "why": "deterministic browser engine fixture"}
    engines.claude_print = lambda _prompt, **options: {
        "session_id": "fixture-lapse", "text": "", "error": REVOKED, "tools": [], "turns": 1,
        "rejection": engines.rejection(lapsed, {"raw_stdout": STREAM}),
        "safe_to_retry": engines._safe_output(lapsed, STREAM)}
    answers = iter(("The activation is waiting for the next quiet point.", "Nothing else failed."))
    engines.codex_exec = lambda _prompt, **options: {
        "session_id": "fixture-other", "reported_session_id": "fixture-other", "text": next(answers),
        "tools": [], "error": None, "usage": {}}
    # One project can answer on the other engine; one is set to the lapsed engine alone and waits.
    for project, routing in (("atlas", f"{lapsed}:opus > {other}"), ("harbor", f"{lapsed}:opus")):
        repo = config.PROJECT_ROOTS[0] / project
        repo.mkdir(parents=True)
        with config.add_project(project, path=repo):
            pass
        projects = config.load_projects()
        projects[project]["routing"] = config.parse_routing(routing)
        config.save_projects(projects)
        S.regen_state_md(project)
        l3.chat_log(project, "user", "Keep the release notes short.", trigger="chat", turn_id=f"{project}-earlier")
        l3.chat_log(project, "assistant", "The release notes stay short.", trigger="chat", turn_id=f"{project}-earlier",
                    engine=lapsed)
    serve()


if __name__ == "__main__":
    main()
