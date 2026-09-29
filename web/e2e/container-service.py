"""Fictional image identity for UI states only; this is not native container acceptance."""
from service_support import configure, serve
from tests.support import SUITE
from altitude import config, platform, state as S

configure()
platform.containerized = lambda: True
platform.container_shell_command = lambda: "podman exec -it --user 1000 --env HOME=/home/altitude fixture-container bash"
platform.CONTAINER_PROJECTS = SUITE / "container-projects"
platform.CONTAINER_PROJECTS.mkdir()
config.RELEASE = {"version": "v0.1.0-rc.2"}
S.write_json(config.ROOT / "settings.json", {"voice": "browser", "operator_name": "Alex"})
serve()
