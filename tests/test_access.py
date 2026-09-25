"""This machine's key: altd creates it privately at start, and its own callers send it."""
import io
import json

from tests.support import AltitudeCase
from tests.test_restart_command import load_script
from altitude import access


class TestMachineKey(AltitudeCase):
    def test_altd_creates_a_private_machine_key_that_the_restart_check_sends(self):
        self.patch(access, "DIR", self.tmp / "access")
        access.prepare()
        key = access.machine_key()
        access.prepare()
        self.assertEqual(access.machine_key(), key)
        self.assertEqual((access.DIR / "machine.key").stat().st_mode & 0o777, 0o600)
        self.assertEqual(access.DIR.stat().st_mode & 0o777, 0o700)
        restart, sent = load_script(), []

        class Reply(io.BytesIO):
            status = 200

        def urlopen(request, **_options):
            sent.append(request.get_header(access.KEY_HEADER.capitalize()))
            return Reply(json.dumps({"projects": []}).encode())

        self.patch(restart, "service_address", new=lambda: ("127.0.0.1", 1))
        self.patch(restart, "urlopen", new=urlopen)
        self.assertEqual(json.loads(restart.fetch("/api/overview")), {"projects": []})
        self.assertEqual(sent, [key])
