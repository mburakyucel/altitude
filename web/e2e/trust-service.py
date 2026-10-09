"""Altitude over real HTTPS with the pairing screen's trust check. The browser runs on this computer, so the service
treats it as another device. Without an argument the identity is a fresh CA this browser does not trust; with one,
it is that folder as `tls.fixture(folder, probe=True)` makes it, for a phone that trusts its CA."""
import sys
from pathlib import Path

from service_support import configure, serve
from tests.support import SUITE
from altitude import config, server, tls


def main():
    configure()
    config.TLS, config.HOST = True, "127.0.0.1"
    if len(sys.argv) > 1:
        config.TLS_DIR = Path(sys.argv[1])
        context, probe = tls._load(config.TLS_DIR), tls._load(config.TLS_DIR / "probe")
    else:
        config.TLS_DIR = SUITE / "private-tls"
        server.tls_init()
        context, probe = tls.check("127.0.0.1"), tls.probe_context("127.0.0.1")
    server.TRUST = tls.TrustCheck(probe, tls.identity(config.TLS_DIR / "ca.crt")["name"])
    server.Handler._local = lambda _handler: False
    serve(context=context)


if __name__ == "__main__":
    main()
