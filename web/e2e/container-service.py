"""Fictional image identity for UI states only; this is not native container acceptance."""
from service_support import configure, serve
from tests.support import SUITE
from altitude import config, platform, server, state as S

configure()
platform.containerized = lambda: True
platform.socket.gethostname = lambda: "fixture-container"
platform.container_shell_command = lambda: "podman exec -it --user 1000 --env HOME=/home/altitude fixture-container bash"
platform.CONTAINER_PROJECTS = SUITE / "container-projects"
platform.CONTAINER_PROJECTS.mkdir()
config.RELEASE = {"version": "v0.1.0-rc.2"}
instance = {"value": "a" * 32}
def identity():
    if instance["value"] is None:
        raise ValueError("fictional unavailable identity")
    return instance["value"]
platform._container_instance = identity
platform.status = lambda: {"ActiveState": "active"}
platform.container_ready = lambda: None
platform._lifecycle_write(instance["value"], False)
S.write_json(config.ROOT / "settings.json", {"voice": "browser", "operator_name": "Alex"})

class Handler(server.Handler):
    def do_POST(self):
        if self.path == "/fixture/replace":
            instance["value"] = "b" * 32
            return self._json(platform.container_lifecycle())
        if self.path == "/fixture/continue":
            return self._json(platform.change_container_lifecycle("continue", instance["value"]))
        if self.path == "/fixture/identity-unavailable":
            instance["value"] = None
            return self._json(platform.container_lifecycle())
        return super().do_POST()

serve(Handler)
