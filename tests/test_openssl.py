"""Stock Mac tool order, bootstrap refusal and service TLS against disposable tooling."""
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
from unittest import mock

from tests.support import AltitudeCase, REPO
from altitude import config, installation, platform, push, tls


class OpenSSLDiscovery(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.real = platform.openssl()
        self.tools = self.tmp / "tools"
        self.tools.mkdir()
        self.arm = self.tmp / "homebrew/opt/openssl@3/bin/openssl"
        self.intel = self.tmp / "local/opt/openssl@3/bin/openssl"
        self.patch(platform, "_MAC_OPENSSL", (str(self.arm), str(self.intel)))
        self.stock = self.tool("openssl", "printf 'LibreSSL 3.3.6\\n'\nexit 0")
        self.setenv("PATH", str(self.tools) + os.pathsep + os.environ["PATH"])

    def tool(self, name, body):
        path = self.tools / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
        return path

    def keg(self, path, version="3.0.0", status=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nif [ \"$1\" = version ]; then\n"
                        f"  printf 'OpenSSL {version} fixture\\n'; exit {status}\nfi\n"
                        f"exec {shlex.quote(self.real)} \"$@\"\n")
        path.chmod(0o755)

    def bootstrap(self):
        # Exercise the entire preflight, substituting only host facts and standard installation locations.
        self.tool("uname", 'case "$1" in -s) echo Darwin;; -m) echo arm64;; esac')
        self.tool("id", "echo 501")
        self.tool("sw_vers", "echo 15.0")
        self.tool("launchctl", "exit 0")
        self.tool("python3.14", "exit 0")
        downloads = self.tmp / "downloads"
        self.tool("curl", f"echo download >> {shlex.quote(str(downloads))}; exit 1")
        script = (REPO / "scripts/install.sh").read_text().replace("@VERSION@", "v0.0.1")
        script = script.replace("/opt/homebrew/opt/openssl@3/bin/openssl", str(self.arm))
        script = script.replace("/usr/local/opt/openssl@3/bin/openssl", str(self.intel))
        result = subprocess.run(["/bin/sh"], input=script, capture_output=True, text=True,
                                env=os.environ.copy(), timeout=20)
        return result, downloads.exists()

    def test_stock_libressl_and_absent_kegs_refuse_before_download_or_profile_change(self):
        self.patch(platform, "_darwin", return_value=True)
        profile = Path.home() / ".zprofile"
        profile.write_text("# existing profile\n")
        before = profile.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "brew install openssl@3"):
            platform.openssl()
        result, downloaded = self.bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(downloaded)
        self.assertIn("brew install openssl@3", result.stderr)
        self.assertIn("no shell-profile change is needed", result.stderr)
        self.assertEqual(profile.read_bytes(), before)
        self.assertFalse((Path.home() / ".local/share/altitude").exists())

    def test_homebrew_kegs_are_discovered_with_libressl_first_and_path_unchanged(self):
        self.patch(platform, "_darwin", return_value=True)
        before = os.environ["PATH"]
        for path in (self.arm, self.intel):
            with self.subTest(path=path.name):
                self.keg(path)
                self.assertEqual(platform.openssl(), str(path))
                result, downloaded = self.bootstrap()
                self.assertTrue(downloaded, result.stderr)
                self.assertIn("could not download", result.stderr)
                self.assertEqual(shutil.which("openssl"), str(self.stock))
                self.assertEqual(os.environ["PATH"], before)
                path.unlink()

    def test_suitable_path_tool_wins_and_unsuitable_or_failed_keg_is_skipped(self):
        self.patch(platform, "_darwin", return_value=True)
        self.keg(self.stock, "3.5.0")
        self.keg(self.arm)
        self.assertEqual(platform.openssl(), str(self.stock))
        self.keg(self.stock, "1.1.1")
        for version, status in (("1.1.1", 0), ("3.0.0", 1)):
            self.keg(self.arm, version, status)
            with self.assertRaises(RuntimeError):
                platform.openssl()
            result, downloaded = self.bootstrap()
            self.assertFalse(downloaded, result.stderr)

    def test_linux_uses_its_path_tool_and_reports_a_missing_tool(self):
        self.patch(platform, "_darwin", return_value=False)
        self.assertEqual(platform.openssl(), str(self.stock))
        with mock.patch.object(platform.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Install OpenSSL"):
                platform.openssl()

    def test_saved_launchagent_path_uses_discovery_for_tls_renewal_doctor_and_signing(self):
        self.keg(self.arm)
        self.patch(platform, "_darwin", return_value=True)
        saved = config.installation_environment()
        definition = plistlib.loads(platform.definition(self.tmp / "application", Path(sys.executable),
                                                        self.tmp / "install.json", saved).encode())
        self.assertEqual(definition["EnvironmentVariables"]["PATH"], saved["PATH"])
        # A daemon launched with only its saved service environment still sees LibreSSL first.
        with mock.patch.dict(os.environ, definition["EnvironmentVariables"], clear=True):
            self.assertEqual(shutil.which("openssl"), str(self.stock))
            directory = self.tmp / "private-tls"
            self.patch(config, "TLS_DIR", directory)
            self.patch(config, "HOST", "localhost")
            tls.initialize()
            tls.check()
            with self.assertRaises(tls.TLSFailure):
                tls._verify(directory, "other.local")
            before = (directory / "server.crt").read_bytes()
            with mock.patch.object(tls, "RENEW_SECONDS", 400 * 24 * 60 * 60):
                tls.check()
            self.assertNotEqual((directory / "server.crt").read_bytes(), before)
            tls.check()
            self.assertIn("BEGIN PUBLIC KEY", push._openssl("pkey", "-in", directory / "server.key", "-pubout").decode())
            with mock.patch.object(platform, "status", side_effect=RuntimeError("fixture service")), \
                    mock.patch.object(platform, "lingering", return_value=None), \
                    mock.patch.object(installation, "_gh_signed_in", return_value=False), \
                    mock.patch.object(installation, "_gh_lacks", return_value=[]):
                doctor = installation.doctor()
            check = next(item for item in doctor["checks"] if item["name"] == "openssl")
            self.assertEqual(check["state"], "configured")
            self.assertEqual(check["detail"], str(self.arm))
