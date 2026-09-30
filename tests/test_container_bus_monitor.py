"""Authorization-attempt detection regressions for the issue #543 VM lane."""
import unittest

import container_bus_monitor as monitor


def message(method, *strings, interface="org.freedesktop.systemd1.Manager"):
    return (f"method call time=1 sender=:1.2 -> destination=org.freedesktop.systemd1; "
            f"interface={interface}; member={method}\n" +
            "".join(f'   string "{value}"\n' for value in strings))


class BusMonitorTests(unittest.TestCase):
    def test_any_unexpected_management_request_fails_before_authorization_result(self):
        for method in ("StopUnit", "StartUnit", "RestartUnit", "StartTransientUnit", "KillUnit"):
            for unit in ("libpod-dead.scope", "arbitrary.service", "podman-123.scope"):
                self.assertIsNone(monitor.classify(message(method, unit, "replace"), {}))
        self.assertIsNone(monitor.classify(message("Stop", "replace", interface="org.freedesktop.systemd1.Unit"), {}))

    def test_positive_control_does_not_allow_other_stops(self):
        self.assertEqual("control", monitor.classify(message("StopUnit", "altitude-fictional-missing.scope", "replace"), {}))
        self.assertIsNone(monitor.classify(message("StopUnit", "altitude-fictional-missing.scope", "isolate"), {}))

    def test_attachment_requires_exact_user_manager_and_own_unit(self):
        identity = {"uid": 1000, "exe": "/usr/lib/systemd/systemd",
                    "argv": ["/usr/lib/systemd/systemd", "--user"],
                    "cgroup": "0::/user.slice/user-1000.slice/user@1000.service/init.scope"}
        call = message("AttachProcessesToUnit", "", "/user.slice/podman-pause-123abc.scope")
        self.assertEqual("same-user-attachment", monitor.classify(call, identity))
        for key, value in {"uid": 0, "exe": "/usr/bin/crun", "argv": ["systemd"], "cgroup": "/other"}.items():
            self.assertIsNone(monitor.classify(call, {**identity, key: value}))
        self.assertIsNone(monitor.classify(call, {"unavailable": "sender exited"}))
        self.assertIsNone(monitor.classify(message("AttachProcessesToUnit", "other.service", "/user.slice/podman-123.scope"), identity))

    def test_login_allowance_requires_actual_root_logind(self):
        call = message("StartTransientUnit", "session-2.scope", "fail", "Description")
        identity = {"uid": 0, "exe": "/usr/lib/systemd/systemd-logind"}
        self.assertEqual("login-session", monitor.classify(call, identity))
        self.assertIsNone(monitor.classify(call, {**identity, "uid": 1000}))
        self.assertIsNone(monitor.classify(message("StartTransientUnit", "libpod-123.scope"), identity))


if __name__ == "__main__":
    unittest.main()
