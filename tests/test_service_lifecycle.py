"""Regression coverage for the Altitude service ownership boundary."""

import unittest

from tests.support import REPO


class TestServiceLifecycle(unittest.TestCase):
    def test_stop_owns_the_entire_service_cgroup(self):
        unit = (REPO / "systemd" / "altitude.service").read_text()
        settings = {
            line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
            for line in unit.splitlines()
            if line.strip() and not line.lstrip().startswith("#") and "=" in line
        }

        self.assertEqual(settings.get("KillMode"), "control-group")
        self.assertNotIn("KillMode=process", unit)


if __name__ == "__main__":
    unittest.main()
