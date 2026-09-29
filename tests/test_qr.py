"""The QR code a phone camera scans to open the certificate share link."""
import hashlib
import unittest

from altitude import qr

# libqrencode 4 output (`qrencode -l M -8`) for the same texts: one hexadecimal number per module row.
REFERENCE = {
    "http://altitude.home.arpa:38001/": (29, [
        "1fd5e37f", "10569041", "1744125d", "1756685d", "17415e5d", "1046b241", "1fd5557f", "001d3a00", "16eaac4b",
        "1c20e671", "054c98a6", "021517e9", "1e6a6c2c", "18365643", "19f5bbef", "088d5582", "056463b2", "010b5526",
        "13e0fd1c", "041cfe84", "0a48f9fc", "001f091f", "1fd55b5a", "105ad111", "17413df6", "175ead15", "175dd321",
        "1047a55a", "1fd91352"]),
    "http://[fd00::1]:40000/": (25, [
        "1fce47f", "1042641", "175c05d", "175095d", "175c45d", "1058341", "1fd557f", "001f300", "17c097c", "02b9722",
        "15c83db", "13ab261", "1cf1b57", "13284aa", "16f327b", "1726059", "14d69f4", "001d51c", "1fc815f", "105131a",
        "1755bff", "1754b67", "175bfa5", "104e579", "1fd327f"]),
}


# Larger symbols, including version 32's exceptional alignment spacing: SHA-256 of libqrencode's module rows,
# "0"/"1" per module, joined by newlines, for "x" repeated this many times.
LARGE = {1455: (32, "6d8abd9d9ff061b69fee83a582ad4d8755f14168250becf8fcfb09413a12a467"),
         2329: (40, "6dccc84f9c3f399420cd47311cd686df06ab1e7cf5572d55b8d07f59bad50a69")}


def rows(modules):
    return [f"{int(''.join('1' if dark else '0' for dark in row), 2):0{(len(row) + 3) // 4}x}" for row in modules]


class TestQR(unittest.TestCase):
    def test_matches_an_independent_encoder_module_for_module(self):
        for text, (size, expected) in REFERENCE.items():
            with self.subTest(text=text):
                modules = qr.matrix(text)
                self.assertEqual(len(modules), size)
                self.assertEqual(rows(modules), expected)

    def test_large_versions_match_the_independent_encoder(self):
        for length, (version, digest) in LARGE.items():
            with self.subTest(version=version):
                modules = qr.matrix("x" * length)
                self.assertEqual(len(modules), version * 4 + 17)
                bits = "\n".join("".join("1" if dark else "0" for dark in row) for row in modules)
                self.assertEqual(hashlib.sha256(bits.encode()).hexdigest(), digest)
        self.assertEqual(qr._alignment(32), [6, 34, 60, 86, 112, 138])

    def test_the_version_grows_with_the_text_and_a_text_beyond_version_40_is_refused(self):
        self.assertEqual(len(qr.matrix("a" * 14)), 21)
        self.assertEqual(len(qr.matrix("a" * 15)), 25)
        self.assertEqual(len(qr.matrix("a" * 2331)), 177)
        with self.assertRaisesRegex(ValueError, "too long"):
            qr.matrix("a" * 2332)

    def test_the_terminal_drawing_holds_every_module_inside_a_light_quiet_zone(self):
        text = "http://192.168.1.20:43567/"
        drawing = qr.terminal(text).splitlines()
        halves = {" ": (False, False), "▀": (True, False), "▄": (False, True), "█": (True, True)}
        for line in drawing:
            self.assertTrue(line.startswith("\x1b[38;5;16;48;5;231m") and line.endswith("\x1b[0m"), repr(line))
        cells = [line[len("\x1b[38;5;16;48;5;231m"):-len("\x1b[0m")] for line in drawing]
        grid = [[halves[c][half] for c in line] for line in cells for half in (0, 1)]
        modules = qr.matrix(text)
        size = len(modules)
        self.assertEqual(len(cells[0]), size + 8)
        self.assertEqual([row[4:4 + size] for row in grid[4:4 + size]], modules)
        self.assertFalse(any(grid[r][c] for r in range(len(grid)) for c in range(size + 8)
                             if not (4 <= r < 4 + size and 4 <= c < 4 + size)))


if __name__ == "__main__":
    unittest.main()
