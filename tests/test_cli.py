"""`alt` sends a service field only when its option is given, so a checkout awaiting activation keeps working
against the older running service, whose handlers refuse fields they do not know."""
import io
import json
import os
import runpy
import urllib.request
from unittest import mock

from tests.support import ALT, AltitudeCase
from altitude import tls


class Sent(Exception):
    pass


class TestServicePayload(AltitudeCase):
    def sent(self, *argv: str, actor: str | None = None) -> tuple[str, dict]:
        """The endpoint and JSON body `alt` sends for one command; the service is never reached."""
        env = {"ALTITUDE_PROJECT": self.project, "ALTITUDE_TASK": "owned", "ALTITUDE_ATTEMPT": "1"}
        service = {"host": "127.0.0.1", "port": 1, "tls": False, "tls_dir": self.tmp, "url": "http://127.0.0.1:1"}

        def urlopen(request, **_):
            raise Sent(request.full_url.rsplit("/api/", 1)[1], json.loads(request.data))
        with mock.patch.dict(os.environ, env), mock.patch.object(tls, "service", return_value=service), \
                mock.patch.object(urllib.request, "urlopen", urlopen), mock.patch("sys.stdin", io.StringIO("text")):
            os.environ.pop("ALTITUDE_ACTOR", None)
            os.environ.update({"ALTITUDE_ACTOR": actor} if actor else {})
            with self.assertRaises(Sent) as sent:
                runpy.run_path(str(ALT))["main"](list(argv))
        return sent.exception.args

    def test_validate_sends_simulator_kvm_and_publish_only_when_given(self):
        base = {"project": self.project, "slug": "owned", "attempt": "1", "command": ["true"]}
        self.assertEqual(self.sent("task", "validate", "--", "true", actor="l2"), ("task/validate", base))
        self.assertEqual(self.sent("task", "validate", "--simulator", "--kvm", "--publish", "8080", "--", "true",
                                   actor="l2"),
                         ("task/validate", {**base, "simulator": True, "kvm": True, "publish": 8080}))

    def test_issue_new_sends_labels_only_when_given(self):
        base = {"project": self.project, "operation": "new", "body": "text", "title": "T"}
        self.assertEqual(self.sent("issue", "new", "--title", "T", "-"), ("issue", base))
        self.assertEqual(self.sent("issue", "new", "--title", "T", "--label", "bug", "-"),
                         ("issue", {**base, "labels": ["bug"]}))
