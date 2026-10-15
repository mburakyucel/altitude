"""Altitude's own engine sign-in from the browser: the real service runs `tests/fake_token_setup.py` as the engine's
setup command on a terminal of its own, so the page walks every state against the real sign-in. A sign-in left open
expires after 15 seconds instead of ten minutes."""
import sys
from pathlib import Path

from service_support import configure, serve
from tests.support import SUITE
from altitude import access, engines
from altitude.engines import token_setup

FAKE = Path(__file__).resolve().parents[2] / "tests/fake_token_setup.py"


def main():
    configure()
    engine = engines.TOKEN_SETUP[0]
    setup = SUITE / "setup-command"
    setup.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    setup.chmod(0o755)
    engines.common.implementation(engine).binary = lambda: str(setup)
    # Signed in once the sign-in saved Altitude's own token; every other engine is installed and signed out.
    engines.sign_in = lambda name: {"signed_in": name == engine and access.engine_token(name) is not None,
                                    "command": engines.SIGN_IN[name][1]}
    token_setup.DEADLINE = 15
    serve()


if __name__ == "__main__":
    main()
