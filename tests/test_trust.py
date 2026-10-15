"""`alt trust` on the hosting computer: real NSS databases in a disposable home folder with NSS's certutil, and a
fixture macOS `security`; the running service is a real HTTPS health endpoint. No host trust store is touched."""
import hashlib
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import unittest
from unittest import mock

from tests.test_tls import HealthServer, ServiceCase
from altitude import config, installation, platform, tls

CERTUTIL = os.environ.get("ALTITUDE_TEST_CERTUTIL") or shutil.which("certutil")


class TrustCase(ServiceCase):
    def setUp(self):
        super().setUp()
        self.home = config.HOME
        self.home.mkdir(parents=True, exist_ok=True)
        health = HealthServer(self, self.service_tls, 4242)
        self.running({"port": health.port})
        self.asked = []
        self.lines = []
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "openssl").symlink_to(platform.openssl())
        self.path(str(self.bin))

    def path(self, value: str):
        patcher = mock.patch.dict(os.environ, {"PATH": value})
        patcher.start()
        self.addCleanup(patcher.stop)

    def answer(self, reply):
        def ask(question):
            self.asked.append(question)
            return reply
        return ask

    def trust(self, reply="y") -> bool:
        self.lines.clear()
        result = tls.trust(out=self.lines.append, ask=self.answer(reply))
        self.output = "\n".join(self.lines)
        return result

    def authority(self) -> dict:
        directory = self.service_tls
        return tls._authority(directory, directory / "ca.crt")


@unittest.skipUnless(CERTUTIL, "needs NSS's certutil (libnss3-tools), as the required check installs")
class TestLinux(TrustCase):
    def setUp(self):
        super().setUp()
        (self.bin / "certutil").symlink_to(CERTUTIL)
        (self.bin / "sh").symlink_to(shutil.which("sh", path="/usr/bin:/bin"))
        patcher = mock.patch.object(platform, "_darwin", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.chrome = self.database(".pki/nssdb")
        self.firefox = self.database(".mozilla/firefox/ab12cd34.default-release")

    def database(self, place: str, *password: str) -> Path:
        folder = self.home / place
        folder.mkdir(parents=True)
        if password:
            secret = self.root / "password"
            secret.write_text(password[0] + "\n")
            subprocess.run([CERTUTIL, "-N", "-d", f"sql:{folder}", "-f", secret], check=True, capture_output=True)
        else:
            subprocess.run([CERTUTIL, "-N", "-d", f"sql:{folder}", "--empty-password"], check=True, capture_output=True)
        return folder

    def trusts(self, folder: Path) -> bool:
        return platform.store_trusts({"path": folder}, self.authority())

    def test_trust_waits_for_yes_and_names_what_it_changes(self):
        authority = self.authority()
        for reply in ("", "n", None):
            with self.subTest(reply=reply):
                self.assertFalse(self.trust(reply))
                self.assertFalse(self.trusts(self.chrome) or self.trusts(self.firefox), "nothing changed")
        self.assertEqual(self.asked, ["Trust it? [y/N] "] * 3)
        self.assertIn("There is no terminal to ask in, so nothing changed.", self.output)
        self.trust("n")
        self.assertEqual(self.lines[-1], "Nothing changed.")
        self.assertEqual(self.lines[0].splitlines(), [
            "Trust Altitude's certificate on this computer?",
            f"  Name:    {authority['name']}",
            f"  SHA-256: {authority['sha256']}",
            f"  Scope:   {tls.describe_scope(authority['scope'])}",
            "It will identify websites only, in:",
            "  Chrome and Chromium                 ~/.pki/nssdb",
            "  Firefox (ab12cd34.default-release)  ~/.mozilla/firefox/ab12cd34.default-release"])
        self.assertTrue(authority["scope"]["permitted"], "the scope shown is the CA's private-name limit")

    def test_yes_trusts_every_browser_store_for_websites_and_prints_the_undo(self):
        self.assertTrue(self.trust("y"))
        self.assertTrue(self.trusts(self.chrome) and self.trusts(self.firefox))
        nickname = self.authority()["nickname"]
        self.assertRegex(nickname, r"^Altitude CA [2-9A-Z]{4} · [0-9A-F]{8}$")
        listing = subprocess.run([CERTUTIL, "-L", "-d", f"sql:{self.chrome}"], capture_output=True, text=True).stdout
        self.assertRegex(listing, rf"{nickname}\s+C,,\s*$", "trusted to identify websites only")
        self.assertEqual(self.lines[1:], [
            "Trusted in Chrome and Chromium and Firefox (ab12cd34.default-release). Restart your browser before "
            "opening Altitude.\nTo undo:\n"
            f"  certutil -D -d sql:\"$HOME/.pki/nssdb\" -n '{nickname}'\n"
            f"  certutil -D -d sql:\"$HOME/.mozilla/firefox/ab12cd34.default-release\" -n '{nickname}'"])

        undo = [line.strip() for line in self.lines[-1].split("To undo:\n", 1)[1].splitlines()]

        self.asked.clear()
        self.assertTrue(self.trust("n"))
        self.assertEqual((self.asked, self.lines), ([], [
            "Already trusted on this computer in: Chrome and Chromium and Firefox (ab12cd34.default-release)."]))

        for command in undo:
            subprocess.run(["sh", "-c", command], check=True, capture_output=True, env={**os.environ, "HOME": str(self.home)})
        self.assertFalse(self.trusts(self.chrome) or self.trusts(self.firefox), "the printed undo removes it")

    def test_a_certificate_imported_by_hand_keeps_its_name_and_its_undo_names_it(self):
        ca = self.service_tls / "ca.crt"
        subprocess.run([CERTUTIL, "-A", "-d", f"sql:{self.chrome}", "-n", self.authority()["name"], "-t", ",,", "-i", ca],
                       check=True, capture_output=True)
        self.assertFalse(self.trusts(self.chrome), "an imported but untrusted CA is not trust")
        self.assertTrue(self.trust("y"))
        self.assertTrue(self.trusts(self.chrome))
        self.assertIn(f"certutil -D -d sql:\"$HOME/.pki/nssdb\" -n '{self.authority()['name']}'", self.output)

    def test_a_store_that_needs_a_password_is_named_and_the_others_still_trust(self):
        locked = self.database("snap/firefox/common/.mozilla/firefox/xy98.default", "fictional-primary-password")
        self.assertFalse(self.trust("y"))
        self.assertTrue(self.trusts(self.chrome) and self.trusts(self.firefox))
        self.assertFalse(self.trusts(locked))
        self.assertIn("Not changed in Firefox (xy98.default): ", self.output)
        self.assertIn("A Firefox profile with a Primary Password needs the certificate imported by hand", self.output)

    def test_an_installed_chrome_gets_the_database_it_reads_but_never_creates(self):
        shutil.rmtree(self.home / ".pki")
        (self.home / "snap/chromium/current").mkdir(parents=True)
        (self.bin / "google-chrome").symlink_to(self.bin / "sh")
        self.assertTrue(self.trust("y"))
        self.assertIn("  Chrome and Chromium                 ~/.pki/nssdb", self.lines[0])
        self.assertIn("  Chromium (snap)                     ~/snap/chromium/current/.pki/nssdb", self.lines[0])
        self.assertTrue(self.trusts(self.chrome) and self.trusts(self.home / "snap/chromium/current/.pki/nssdb"))
        self.assertEqual(self.chrome.stat().st_mode & 0o777, 0o700)

    def test_a_flatpak_chrome_is_left_to_its_own_import(self):
        (self.home / ".var/app/com.google.Chrome").mkdir(parents=True)
        self.assertTrue(self.trust("y"))
        self.assertEqual(self.lines[-1], "Not changed in Chrome (Flatpak): it keeps its own store; import by hand.")

    def test_without_a_browser_store_or_certutil_nothing_is_asked(self):
        shutil.rmtree(self.home / ".pki")
        shutil.rmtree(self.home / ".mozilla")
        self.assertFalse(self.trust("y"))
        self.assertEqual((self.asked, self.lines), ([], [tls.NO_STORE]))
        self.database(".pki/nssdb")
        (self.bin / "certutil").unlink()
        with mock.patch.object(platform, "package_manager", return_value="apt"):
            self.assertFalse(self.trust("y"))
        self.assertEqual((self.asked, self.lines), ([], [
            "Chrome and Firefox keep certificates in databases that need certutil, which is not installed. Install it "
            "with sudo apt install libnss3-tools, then run ~/.local/bin/alt trust again."]))

    def test_only_altitudes_own_certificate_from_the_running_service_is_trusted(self):
        (self.service_tls / ".altitude-managed").unlink()
        with self.assertRaisesRegex(tls.TLSFailure, "^Altitude did not create this certificate"):
            self.trust("y")
        self.running({"tls": False})
        with self.assertRaisesRegex(tls.TLSFailure, "serves plain HTTP"):
            self.trust("y")
        with mock.patch.object(platform, "containerized", return_value=True), \
             self.assertRaisesRegex(tls.TLSFailure, "This container cannot change the host's browsers"):
            self.trust("y")
        self.assertEqual(self.asked, [])
        self.assertFalse(self.trusts(self.chrome))

    def test_doctor_names_each_store_and_offers_alt_trust_until_the_first_browser_pairs(self):
        hosting = {"computer": "a fictional computer", "state": "running", "host": "127.0.0.1", "tls_dir": str(self.service_tls), "url": "https://127.0.0.1:9443", "verified": True}
        with mock.patch.object(installation, "hosting_status", return_value=hosting), \
             mock.patch.object(installation, "prerequisites", return_value=[]), \
             mock.patch("altitude.platform.status", side_effect=RuntimeError("no service in tests")), \
             mock.patch("altitude.platform.lingering", return_value=None), \
             mock.patch("altitude.access.devices", return_value=[]), \
             mock.patch("altitude.access.code_expires", return_value=None):
            report = installation.doctor()
            computer = report["certificate_trust"]["this_computer"]
            self.assertEqual(computer, {"stores": [
                {"label": "Chrome and Chromium", "path": str(self.chrome), "trusted": False},
                {"label": "Firefox (ab12cd34.default-release)", "path": str(self.firefox), "trusted": False}]})
            self.assertEqual(report["next"], ["Next: trust Altitude's certificate on this computer: alt trust, then run alt pair."])
            text = installation.describe_doctor(report)
            self.assertIn("Certificate trust on this computer: not in Chrome and Chromium, Firefox "
                          "(ab12cd34.default-release)", text)
            self.assertIn("Browser certificate trust: unverified until a browser opens Altitude without a warning.", text)
            subprocess.run([CERTUTIL, "-A", "-d", f"sql:{self.chrome}", "-n", self.authority()["name"], "-t", "C,,", "-i",
                            self.service_tls / "ca.crt"], check=True, capture_output=True)
            report = installation.doctor()
            self.assertIn("Certificate trust on this computer: trusted in Chrome and Chromium; not in Firefox "
                          "(ab12cd34.default-release)", installation.describe_doctor(report))
            self.trust("y")
            report = installation.doctor()
            self.assertEqual(report["next"], ["Next: compare the certificate below with what your browser shows, "
                                              "trust it, then run alt pair."])
            (self.bin / "certutil").unlink()
            report = installation.doctor()
        self.assertIn("Certificate trust on this computer: Chrome and Firefox keep certificates in databases that need "
                      "certutil", installation.describe_doctor(report))
        self.assertIn("alt trust", report["next"][0], "alt trust names the package to install")


class TestMac(TrustCase):
    """macOS's security command as a fixture: it trusts once add-trusted-cert succeeds, and refuses when told to."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(platform, "_darwin", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = self.root / "security-calls"
        self.state = self.root / "keychain-trusts"
        security = self.bin / "security"
        security.write_text(f"""#!/bin/sh
echo "$@" >> {self.calls}
case "$1" in
  verify-cert) [ -e {self.state} ] ;;
  add-trusted-cert) [ -z "$FIXTURE_DENY" ] || {{ echo "SecTrustSettingsSetTrustSettings: no user interaction was possible." >&2; exit 1; }}
                    : > {self.state} ;;
esac
""")
        security.chmod(0o755)

    def test_the_login_keychain_trusts_it_for_websites_after_macos_asks(self):
        authority = self.authority()
        self.assertTrue(self.trust("y"))
        self.assertIn("  macOS login keychain (Safari, Chrome, Edge, Firefox)  ~/Library/Keychains/login.keychain-db\n"
                      "macOS will ask for your password in a window.", self.lines[0])
        calls = self.calls.read_text().splitlines()
        self.assertTrue(calls[0].startswith(f"verify-cert -c {self.service_tls / 'server.crt'} -c "))
        self.assertTrue(calls[0].endswith(" -p ssl -L -q"))
        added = calls[1].split()
        self.assertEqual(added[:7], ["add-trusted-cert", "-r", "trustRoot", "-p", "ssl", "-k",
                                     str(self.home / "Library/Keychains/login.keychain-db")])
        self.assertEqual(Path(added[7]).name, "ca.crt", "the certificate the service proved, copied while it ran")
        self.assertFalse(Path(added[7]).exists())
        sha1 = hashlib.sha1(ssl.PEM_cert_to_DER_cert((self.service_tls / "ca.crt").read_text())).hexdigest().upper()
        self.assertEqual(authority["sha1"], sha1)
        self.assertEqual(self.lines[-1], "Trusted in macOS login keychain (Safari, Chrome, Edge, Firefox). Restart your "
                         "browser before opening Altitude.\nTo undo:\n"
                         f"  security delete-certificate -Z {sha1} -t \"$HOME/Library/Keychains/login.keychain-db\"")
        self.assertTrue(self.trust("n"))
        self.assertEqual(self.lines, ["Already trusted on this computer in: macOS login keychain (Safari, Chrome, Edge, "
                                      "Firefox)."])

    def test_a_remote_terminal_is_refused_by_macos_and_pointed_to_the_desktop(self):
        with mock.patch.dict(os.environ, {"FIXTURE_DENY": "1"}):
            self.assertFalse(self.trust("y"))
        self.assertEqual(self.lines[-1], "Not changed in macOS login keychain (Safari, Chrome, Edge, Firefox): "
                         "SecTrustSettingsSetTrustSettings: no user interaction was possible. macOS confirms this in a "
                         "window on the Mac's own desktop, which a remote terminal cannot show; run alt trust there, or "
                         "follow the Mac steps on the pair page.")


if __name__ == "__main__":
    unittest.main()
