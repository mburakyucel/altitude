"""Repeat fictional browser journeys in an admitted validation run, with disposable tools and evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from altitude import platform


def validate(specs: list[str]) -> int:
    if os.environ.get("ALTITUDE_VALIDATION") != "1" or not os.environ.get("VALIDATION_RESULTS"):
        raise ValueError("Run through alt task validate; a worker shell is not the fictional-browser runner")
    results = Path(os.environ["VALIDATION_RESULTS"])
    web = Path(__file__).resolve().parents[1] / "web"
    for spec in specs:
        path = Path(spec)
        if (not spec.endswith('.pw.ts') or path.is_absolute() or '..' in path.parts
                or spec.startswith('-') or not (web / 'e2e' / path).is_file()):
            raise ValueError("UI_ARGS accepts existing relative .pw.ts files only; configuration is shared")
    home, temp = Path(os.environ["HOME"]), Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
    env = {**os.environ, **platform.validation_browser_environment(temp),
           "npm_config_cache": str(temp / "npm"), "XDG_CACHE_HOME": str(home / "cache"),
           "XDG_CONFIG_HOME": str(home / "config"), "PLAYWRIGHT_BROWSERS_PATH": str(home / "browsers"),
           "ALTITUDE_BROWSER_EVIDENCE": str(results / "browser.json"), "ALTITUDE_UI_HEADLESS_SHELL": "0"}
    container = platform.validation_in_container()
    configuration = "playwright.validation.config.ts" if container else "playwright.config.ts"
    package = json.loads((web / "package.json").read_text())["packageManager"]
    pnpm = ["pnpm"] if shutil.which("pnpm") else ["npm", "exec", "--yes", "--package=" + package, "--", "pnpm"]
    stages = [
        ("dependencies", pnpm + ["install", "--frozen-lockfile", "--store-dir", str(temp / "pnpm-store")], 600),
        ("browser", pnpm + ["exec", "playwright", "install", "chromium"], 600),
        ("build", pnpm + ["build"], 300),
        ("preflight", pnpm + ["exec", "playwright", "test", "--config", configuration,
                              "browser-preflight.pw.ts"], 120),
        ("journeys", pnpm + ["exec", "playwright", "test", "--config", configuration, *specs], 1200),
    ]
    evidence = {"configuration": configuration, "host": platform.host_identity(), "stages": [],
                "boundary": "container and browser" if container else "Seatbelt fictional harness; no inner browser sandbox",
                "identity": "corroborate with the daemon's candidate/profile/job receipt", "passed": False}
    code = 1
    try:
        if not container:
            probes = platform.validation_browser_probe(web.parent)
            (results / "native-probes.json").write_text(json.dumps(probes, indent=2) + "\n")
            if not probes["passed"]:
                return 1
        for name, command, timeout in stages:
            if name in ("preflight", "journeys"):
                env["ALTITUDE_BROWSER_EVIDENCE"] = str(results / f"{name}.json")
                env["ALTITUDE_UI_RESULTS"] = str(results / name)
            with (results / f"{name}.log").open("w") as log:
                try:
                    run = subprocess.Popen(command, cwd=web, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                    try:
                        status = run.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        # The unreaped stage leader pins this owned group; never target a name or another job.
                        os.killpg(run.pid, signal.SIGKILL)
                        run.wait(timeout=10)
                        status = 124
                except subprocess.TimeoutExpired:
                    status = 124
            evidence["stages"].append({"name": name, "command": command, "timeout": timeout, "exit": status})
            if status:
                code = status
                break
        else:
            code = 0
            evidence["passed"] = True
    except (OSError, subprocess.SubprocessError) as exc:
        evidence["error"] = str(exc)
        code = 1
    finally:
        (results / "ui-validation.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(validate(sys.argv[1:]))
