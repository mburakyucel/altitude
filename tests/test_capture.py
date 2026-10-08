"""Validation captures: the GIF check Altitude serves through, and the encoder's budget and failures.

`gif()` hand-encodes small valid animated GIFs for fixtures. Encoding through the host's `ffmpeg` runs where it is
installed; without it the capability's unavailable outcome is what every lane reports.
"""
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, mock, skipUnless

from altitude import capture as C


def gif(width: int = 12, height: int = 8, frames: int = 3, delay: int = 50) -> bytes:
    """An animated GIF of `frames` solid frames, each shown `delay` hundredths of a second. Every pixel's code follows
    a clear code, so the LZW stream stays 3 bits a code and needs no table."""
    def image(colour: int) -> bytes:
        bits, count = 0, 0
        for code in [4, colour] * (width * height) + [5]:
            bits |= code << count
            count += 3
        data = bits.to_bytes((count + 7) // 8, "little")
        blocks = b"".join(bytes([len(data[i:i + 255])]) + data[i:i + 255] for i in range(0, len(data), 255))
        return (b"\x21\xf9\x04\x00" + struct.pack("<H", delay) + b"\x00\x00"
                + b"\x2c" + struct.pack("<HHHH", 0, 0, width, height) + b"\x00\x02" + blocks + b"\x00")
    palette = bytes([0x18, 0x18, 0x18, 0xd8, 0xd8, 0xd8, 0x3a, 0x7b, 0xd5, 0xe0, 0x6c, 0x2b])
    return (b"GIF89a" + struct.pack("<HH", width, height) + b"\xf1\x00\x00" + palette
            + b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
            + b"".join(image(index % 3 + 1) for index in range(frames)) + b"\x3b")


class TestDescribe(TestCase):
    def test_a_capture_reports_its_size_dimensions_frames_and_playback(self):
        data = gif(120, 80, frames=3, delay=50)
        self.assertEqual(C.describe(data), {"bytes": len(data), "width": 120, "height": 80, "frames": 3, "seconds": 1.5})

    def test_anything_but_one_complete_gif_within_its_limits_is_refused(self):
        data = gif()
        frame = b"\x2c" + struct.pack("<HHHH", 0, 0, 12, 8) + b"\x00"  # every fixture frame's descriptor
        at = data.index(frame) + len(frame)
        for name, bad in (("not a GIF", b"\x89PNG\r\n\x1a\n" + data[6:]), ("trailing data", data + b"<script>"),
                          ("truncated", data[:-5]), ("no frames", data[:13 + 12 + 19] + b"\x3b"),
                          ("too wide", data[:6] + struct.pack("<H", C.MAX_SIDE + 1) + data[8:]),
                          ("too many frames", gif(2, 2, frames=C.MAX_FRAMES + 1)),
                          ("frame wider than its screen", data.replace(frame, frame[:5] + struct.pack("<H", 65535) + frame[7:])),
                          ("frame offset past its screen", data.replace(frame, frame[:1] + struct.pack("<H", 1) + frame[3:])),
                          ("frame code size", data.replace(frame + b"\x02", frame + b"\x0c")),
                          ("frame without data", gif(frames=1)[:at] + b"\x02\x00\x3b")):
            with self.subTest(name), self.assertRaises(ValueError):
                C.describe(bad)
        with mock.patch.object(C, "BUDGET", len(data) - 1), self.assertRaises(ValueError):
            C.describe(data)


class TestUnavailable(TestCase):
    def test_without_ffmpeg_a_capture_is_unavailable_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(C.shutil, "which", return_value=None):
            target = Path(folder) / "out.gif"
            (Path(folder) / "in.mp4").write_bytes(b"fixture")
            with self.assertRaisesRegex(C.CaptureError, C.UNAVAILABLE):
                C.video(Path(folder) / "in.mp4", target, C.PHONE)
            with self.assertRaisesRegex(C.CaptureError, C.UNAVAILABLE):
                C.terminal([(0.0, "booting the VM")], target, "installation-vm")
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["in.mp4"], "no frames are left")

    def test_a_lane_that_wrote_nothing_has_no_capture(self):
        with self.assertRaisesRegex(C.CaptureError, "no output"):
            C.terminal([], Path("unused.gif"), "installation-vm")


@skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed on this host")
class TestEncode(TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)

    def test_a_long_terminal_run_plays_in_at_most_a_minute_within_the_budget(self):
        steps = ("booting the VM", "running the all phase", "restarting the VM")
        lines = [(0.0, steps[0]), (40.0, "guest provisioned"), (41.0, steps[1])]
        lines += [(42.0 + i * 1.5, f"\x1b[32mok\x1b[0m step {i}\tinstalling ünïcode " + "x" * 120) for i in range(400)]
        lines += [(700.0, steps[2]), (760.0, "VM deleted; passed")]
        made = C.terminal(lines, self.folder / "vm.gif", "installation-vm 0123456789ab", steps)
        self.assertEqual(made, C.describe((self.folder / "vm.gif").read_bytes()))
        self.assertEqual((made["width"], made["height"]), (480, 312))
        self.assertLessEqual(made["bytes"], C.BUDGET)
        self.assertLessEqual(made["frames"], C.MAX_FRAMES)
        self.assertLessEqual(made["seconds"], C.PLAYBACK + 1)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["vm.gif"])

    def test_a_run_with_more_changes_than_frames_keeps_evenly_spaced_frames_within_the_limit(self):
        made = C.terminal([(i * 0.9, f"[harness] line {i} " + "x" * (i % 60)) for i in range(1000)],
                          self.folder / "vm.gif", "installation-vm")
        self.assertEqual(made["frames"], C.MAX_FRAMES)
        self.assertLessEqual(made["bytes"], C.BUDGET)

    def test_a_recording_keeps_changed_frames_at_its_width_and_still_screens_are_held_briefly(self):
        source = self.folder / "phone.mp4"
        # Four seconds of motion, then six still seconds: the still part is held at most 2 s.
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=1170x2532:rate=30:duration=4", "-f", "lavfi", "-i",
                        "color=c=gray:size=1170x2532:rate=30:duration=6", "-filter_complex", "[0][1]concat=n=2:v=1",
                        "-pix_fmt", "yuv420p", str(source)], check=True, timeout=120)
        made = C.video(source, self.folder / "phone.gif", C.PHONE)
        self.assertEqual(made["width"], C.PHONE)
        self.assertLessEqual(made["bytes"], C.BUDGET)
        self.assertLess(made["seconds"], 7, "the still screen is not shown for its full six seconds")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["phone.gif", "phone.mp4"],
                         "frames made beside the recording are removed")

    def test_a_capture_that_does_not_fit_after_one_smaller_try_is_not_kept(self):
        with mock.patch.object(C, "BUDGET", 2048):
            with self.assertRaisesRegex(C.CaptureError, "budget even at three-quarter size"):
                C.terminal([(i * 0.5, f"line {i} " + "#" * 70) for i in range(200)], self.folder / "vm.gif", "lane")
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_the_command_line_reports_a_capture_or_why_there_is_none_and_exits_zero(self):
        absent = subprocess.run(["python3", "-m", "altitude.capture", str(self.folder / "absent.webm"),
                                 str(self.folder / "x.gif"), "--width", "390"], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parents[1], timeout=120)
        self.assertEqual(absent.returncode, 0)
        self.assertIn('"none"', absent.stdout)
