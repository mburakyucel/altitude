"""Real bounded raster conversion and durable private image-reference ownership."""
from __future__ import annotations

import base64
import ctypes as C
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path
from unittest import mock

from tests.support import AltitudeCase
from altitude import config, images, platform, state as S


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(width=2, height=3, *, extra=b"") -> bytes:
    # A different color per pixel makes orientation and exact screenshot preservation observable.
    pixels = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0, 0, 255, 255, 255, 0, 255])
    scanlines = b"".join(b"\0" + pixels[offset:offset + 6] for offset in range(0, len(pixels), 6))
    return (images._PNG + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + extra + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))


def upload(raw: bytes | None = None, name="screen.png") -> dict:
    return {"name": name, "data": base64.b64encode(png() if raw is None else raw).decode()}


def exif(orientation: int) -> bytes:
    return b"II" + struct.pack("<HIH", 42, 8, 1) + struct.pack("<HHIHHI", 274, 3, 1, orientation, 0, 0)


def rgb_profile(*, linear=False) -> bytes:
    """Generate a fictional ICC profile using the local color capability, without external assets."""
    lib = C.CDLL(platform.find_library("lcms2"))
    lib.cmsCreate_sRGBProfile.restype = C.c_void_p
    if linear:
        class xyY(C.Structure):
            _fields_ = [("x", C.c_double), ("y", C.c_double), ("Y", C.c_double)]

        lib.cmsBuildGamma.argtypes = [C.c_void_p, C.c_double]
        lib.cmsBuildGamma.restype = C.c_void_p
        lib.cmsCreateRGBProfile.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p]
        lib.cmsCreateRGBProfile.restype = C.c_void_p
        curve = lib.cmsBuildGamma(None, 1.0)
        curves = (C.c_void_p * 3)(curve, curve, curve)
        white = xyY(0.3127, 0.3290, 1)
        primaries = (xyY * 3)(xyY(0.64, 0.33, 1), xyY(0.30, 0.60, 1), xyY(0.15, 0.06, 1))
        profile = lib.cmsCreateRGBProfile(C.byref(white), C.byref(primaries), curves)
        lib.cmsFreeToneCurve.argtypes = [C.c_void_p]
        lib.cmsFreeToneCurve(curve)
    else:
        profile = lib.cmsCreate_sRGBProfile()
    lib.cmsSaveProfileToMem.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(C.c_uint32)]
    size = C.c_uint32()
    lib.cmsSaveProfileToMem(profile, None, C.byref(size))
    output = C.create_string_buffer(size.value)
    lib.cmsSaveProfileToMem(profile, output, C.byref(size))
    lib.cmsCloseProfile.argtypes = [C.c_void_p]
    lib.cmsCloseProfile(profile)
    return output.raw


class TestImages(AltitudeCase):
    def commit(self, refs, *, path=None):
        path = path or config.project_dir(self.project) / "chat.jsonl"
        S.atomic_write(path, json.dumps({"id": "message", "images": refs}) + "\n")

    def store(self, *uploads, task=None):
        return images.store(self.project, list(uploads or [upload()]), message_id="message", task=task)

    def convert_fixture(self, source: bytes, codec: str) -> bytes:
        source_path = self.tmp / "fixture.png"
        source_path.write_bytes(source)
        output = self.tmp / ("fixture.jpg" if codec == "mjpeg" else "fixture.webp")
        result = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(source_path),
                                 "-threads", "1", "-c:v", codec, str(output)], capture_output=True, timeout=15)
        if codec == "libwebp" and b"Unknown encoder" in result.stderr:  # Homebrew's ffmpeg decodes WebP only
            result = subprocess.run(["cwebp", "-quiet", str(source_path), "-o", str(output)], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return output.read_bytes()

    def pixels(self, raw: bytes, *, alpha=False) -> bytes:
        path = self.tmp / "pixels-image"
        path.write_bytes(raw)
        result = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-threads", "1",
                                 "-pix_fmt", "rgba" if alpha else "rgb24", "-f", "rawvideo", "pipe:1"],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout

    def test_png_pixels_metadata_provenance_modes_and_committed_access(self):
        original = png(extra=chunk(b"tEXt", b"Location\0private location"))
        refs = self.store(upload(original, "<screen>\x00.png"))
        ref = refs[0]
        with self.assertRaises(images.ImageError):
            images.read(self.project, ref["id"])
        prepared = images.resolve(self.project, refs, committed=False)[0]
        image_path = Path(prepared["path"])
        self.assertEqual(image_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(image_path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(image_path.parent.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual({p.name for p in image_path.parent.iterdir()}, {"metadata.json", "image.png"})
        self.assertEqual(ref["name"], "<screen>.png")
        self.assertEqual((ref["source_message_id"], ref["source_task"], ref["project"]), ("message", None, self.project))
        self.commit(refs)
        raw, saved = images.read(self.project, ref["id"])
        self.assertEqual(saved, ref)
        self.assertEqual(images.lookup(self.project, [ref["id"]]), refs)
        self.assertEqual(ref["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotIn(b"private location", raw)
        self.assertNotIn(b"tEXt", raw)
        self.assertEqual(self.pixels(raw), self.pixels(original))

    def test_static_jpeg_webp_and_all_exif_orientations_normalize(self):
        jpeg = self.convert_fixture(png(), "mjpeg")
        webp = self.convert_fixture(png(), "libwebp")
        refs = self.store(upload(jpeg, "wrong.png"), upload(webp, "wrong.jpeg"))
        self.assertEqual([r["mime_type"] for r in refs], ["image/jpeg", "image/png"])
        for orientation in range(1, 9):
            with self.subTest(orientation=orientation):
                tiff = exif(orientation)
                source = png(extra=chunk(b"eXIf", tiff))
                ref = self.store(upload(source))[0]
                data = Path(images.resolve(self.project, [ref], committed=False)[0]["path"]).read_bytes()
                self.assertEqual((ref["width"], ref["height"]), (3, 2) if orientation >= 5 else (2, 3))
                self.assertNotIn(b"eXIf", data)
                if orientation == 6:
                    initial = self.pixels(png())
                    expected = b"".join(initial[i * 3:i * 3 + 3] for i in (4, 2, 0, 5, 3, 1))
                    self.assertEqual(self.pixels(data), expected)
        metadata = b"Exif\0\0" + exif(6) + b"private location"
        rotated_jpeg = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(metadata) + 2) + metadata + jpeg[2:]
        ref = self.store(upload(rotated_jpeg))[0]
        self.assertEqual((ref["width"], ref["height"]), (3, 2))
        result = Path(images.resolve(self.project, [ref], committed=False)[0]["path"]).read_bytes()
        self.assertNotIn(b"Exif", result)
        self.assertNotIn(b"private location", result)

    def test_rgb_icc_color_transform_preserves_alpha_orientation_and_cleans_staging(self):
        profile = rgb_profile(linear=True)
        pixels = bytes((64, 128, 192, 128)) * 6
        source = (images._PNG + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 3, 8, 6, 0, 0, 0))
                  + chunk(b"iCCP", b"Fictional linear RGB\0\0" + zlib.compress(profile))
                  + chunk(b"eXIf", exif(6))
                  + chunk(b"IDAT", zlib.compress(b"".join(b"\0" + pixels[i:i + 8] for i in range(0, 24, 8))))
                  + chunk(b"IEND", b""))
        ref = self.store(upload(source))[0]
        path = Path(images.resolve(self.project, [ref], committed=False)[0]["path"])
        normalized = path.read_bytes()
        self.assertEqual((ref["width"], ref["height"]), (3, 2))
        self.assertEqual(self.pixels(normalized, alpha=True), bytes((137, 188, 225, 128)) * 6)
        self.assertNotIn(b"iCCP", normalized)
        self.assertNotIn(b"eXIf", normalized)
        self.assertEqual({file.name for file in path.parent.iterdir()}, {"image.png", "metadata.json"})
        # Standard tagged sRGB screenshots remain exact; profiles are removed only after conversion.
        srgb = png(extra=chunk(b"iCCP", b"sRGB\0\0" + zlib.compress(rgb_profile())))
        ref = self.store(upload(srgb))[0]
        normalized = Path(images.resolve(self.project, [ref], committed=False)[0]["path"]).read_bytes()
        self.assertEqual(self.pixels(normalized), self.pixels(png()))

    def test_jpeg_and_webp_rgb_profiles_are_consumed_before_metadata_removal(self):
        profile = rgb_profile()
        jpeg = self.convert_fixture(png(), "mjpeg")
        metadata = b"ICC_PROFILE\0\1\1" + profile
        tagged_jpeg = jpeg[:2] + b"\xff\xe2" + struct.pack(">H", len(metadata) + 2) + metadata + jpeg[2:]
        webp = self.convert_fixture(png(), "libwebp")
        canvas = b"VP8X" + struct.pack("<I", 10) + b"\x20\0\0\0\1\0\0\2\0\0"
        icc = b"ICCP" + struct.pack("<I", len(profile)) + profile + (b"\0" if len(profile) % 2 else b"")
        contents = b"WEBP" + canvas + icc + webp[12:]
        tagged_webp = b"RIFF" + struct.pack("<I", len(contents)) + contents
        for original, tagged in ((jpeg, tagged_jpeg), (webp, tagged_webp)):
            with self.subTest(signature=tagged[:4]):
                ref = self.store(upload(tagged))[0]
                normalized = Path(images.resolve(self.project, [ref], committed=False)[0]["path"]).read_bytes()
                self.assertNotIn(profile, normalized)
                self.assertEqual(len(self.pixels(normalized)), len(self.pixels(original)))

    def test_profile_capability_and_unsupported_profile_errors_preserve_input_bounds(self):
        profile = rgb_profile()
        source = png(extra=chunk(b"iCCP", b"sRGB\0\0" + zlib.compress(profile)))
        with mock.patch.object(platform, "find_library", return_value=None):
            with self.assertRaises(images.ImageError) as unavailable:
                self.store(upload(source))
            self.assertEqual(unavailable.exception.status, 422)
            self.assertIn("color converter", str(unavailable.exception))
            self.assertTrue(self.store())
        invalid = [png(extra=chunk(b"gAMA", struct.pack(">I", 100000))),
                   png(extra=chunk(b"cICP", bytes((9, 16, 0, 1)))),
                   png(extra=chunk(b"iCCP", b"bad\0\0" + zlib.compress(b"not an ICC profile"))),
                   png(extra=chunk(b"iCCP", b"large\0\0" + zlib.compress(b"X" * (images.MAX_PROFILE_BYTES + 1))))]
        for image in invalid:
            with self.assertRaisesRegex(images.ImageError, "color profile"):
                self.store(upload(image))

    def test_rejects_other_animation_truncation_crc_and_dimensions_before_decode(self):
        webp = self.convert_fixture(png(), "libwebp")
        animated_webp = b"RIFF" + struct.pack("<I", 4 + 14) + b"WEBP" + b"ANIM" + struct.pack("<I", 6) + b"\0" * 6
        cases = [b"<svg/>", b"GIF89a", png()[:-1], png() + b"hidden", png()[:-5] + b"bad!!",
                 png() + chunk(b"IEND", b""), png(extra=chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 3, 8, 2, 0, 0, 0))),
                 png(extra=chunk(b"acTL", struct.pack(">II", 2, 0))), animated_webp,
                 webp[:-1], png(8193, 3), png(6000, 6000)]
        with mock.patch.object(images.subprocess, "run") as decoder:
            for raw in cases:
                with self.subTest(prefix=raw[:12], size=len(raw)):
                    with self.assertRaises(images.ImageError):
                        self.store(upload(raw))
            decoder.assert_not_called()
        root = config.project_dir(self.project) / "images"
        self.assertEqual(list(root.iterdir()), [])

    def test_decode_failure_and_full_batch_rejection_retain_no_files(self):
        invalid = (images._PNG + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 3, 8, 2, 0, 0, 0))
                   + chunk(b"IDAT", b"not a zlib stream") + chunk(b"IEND", b""))
        with self.assertRaises(images.ImageError):
            self.store(upload(), upload(invalid))
        self.assertEqual(list((config.project_dir(self.project) / "images").iterdir()), [])

    def test_count_encoded_decoded_and_aggregate_limits(self):
        cases = [[], [upload()] * 5, [{"data": "?"}], [{"data": "A" * (4 * ((images.MAX_BYTES + 2) // 3) + 1)}]]
        for uploads in cases:
            with self.subTest(count=len(uploads)):
                with self.assertRaises(images.ImageError):
                    images.store(self.project, uploads, message_id="message")
        with mock.patch.object(images, "MAX_TOTAL_BYTES", len(png()) * 2 - 1):
            with self.assertRaisesRegex(images.ImageError, "20 MiB"):
                self.store(upload(), upload())
        # This tiny input grows on canonical encoding, exercising both actual output ceilings.
        with mock.patch.object(images, "MAX_TOTAL_BYTES", len(png()) * 2):
            with self.assertRaisesRegex(images.ImageError, "Prepared images"):
                self.store(upload(), upload())
        with mock.patch.object(images, "MAX_BYTES", 90):
            with self.assertRaises(images.ImageError) as too_large:
                self.store()
            self.assertEqual(too_large.exception.status, 413)
        self.assertEqual(list((config.project_dir(self.project) / "images").iterdir()), [])

    def test_interrupted_batch_publication_removes_every_unaccepted_canonical_file(self):
        rename = images.os.rename
        count = 0

        def fail_second_move(source, destination):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("fictional storage interruption")
            return rename(source, destination)

        with mock.patch.object(images.os, "rename", side_effect=fail_second_move):
            with self.assertRaises(OSError):
                self.store(upload(), upload())
        self.assertEqual(list((config.project_dir(self.project) / "images").iterdir()), [])

    def test_converter_missing_timeout_and_process_bounds(self):
        with mock.patch.object(images.shutil, "which", return_value=None):
            self.assertFalse(images.capability()["available"])
            with self.assertRaises(images.ImageError) as raised:
                self.store()
            self.assertEqual(raised.exception.status, 422)
        with mock.patch.object(images, "capability", return_value={"available": True}), \
                mock.patch.object(images.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 15)) as run:
            with self.assertRaisesRegex(images.ImageError, "too long"):
                self.store()
            command = run.call_args.args[0]
            self.assertEqual(command[3], str(1 << 30))  # memory: RLIMIT_AS on Linux, a footprint watcher on macOS
            self.assertIn("RLIMIT_AS" if platform.sys.platform != "darwin" else "proc_pid_rusage", command[2])
            self.assertIn("RLIMIT_CPU", command[2])
            self.assertIn("RLIMIT_FSIZE", command[2])
            self.assertLessEqual(run.call_args.kwargs["timeout"], 15)
            self.assertGreater(run.call_args.kwargs["timeout"], 14)
            self.assertIn("-noautorotate", command)
            self.assertIn("-map_metadata", command)
        self.assertEqual(list((config.project_dir(self.project) / "images").iterdir()), [])

    def test_codec_probe_failure_recovers_without_clearing_cache(self):
        run = images.subprocess.run
        for failure in (subprocess.TimeoutExpired("fixture", 5), OSError("fixture unavailable"),
                        subprocess.CalledProcessError(1, "fixture")):
            with self.subTest(failure=type(failure).__name__):
                images._codecs.cache_clear()
                self.addCleanup(images._codecs.cache_clear)
                probes = []
                root = config.project_dir(self.project) / "images"
                existing = set(root.iterdir()) if root.exists() else set()

                def fail_once(command, **kwargs):
                    if "-codecs" in command:
                        probes.append(command)
                        if len(probes) == 1:
                            if isinstance(failure, subprocess.CalledProcessError):
                                return run([sys.executable, "-c", "raise SystemExit(1)"], **kwargs)
                            raise failure
                    return run(command, **kwargs)

                with mock.patch.object(images.subprocess, "run", side_effect=fail_once):
                    with self.assertRaisesRegex(images.ImageError, "Image input unavailable") as raised:
                        self.store()
                    self.assertEqual(raised.exception.status, 422)
                    self.assertEqual(set(root.iterdir()), existing)
                    refs = self.store()
                    self.commit(refs)
                    raw, saved = images.read(self.project, refs[0]["id"])
                    self.assertEqual(self.pixels(raw), self.pixels(png()))
                    self.assertEqual((saved["width"], saved["height"]), (2, 3))
                    self.assertTrue(images.capability()["available"])
                    self.assertEqual(len(probes), 2)
                    self.assertEqual(probes[0], probes[1])

    def test_completed_codec_probe_keeps_unsupported_converter_unavailable(self):
        images._codecs.cache_clear()
        self.addCleanup(images._codecs.cache_clear)
        result = subprocess.CompletedProcess("fixture", 0, stdout=" DE png\n DE mjpeg\n")
        with mock.patch.object(images.subprocess, "run", return_value=result) as run:
            state = images.capability()
            self.assertFalse(state["available"])
            self.assertIn("Image input unavailable", state["reason"])
            with self.assertRaisesRegex(images.ImageError, "Image input unavailable") as raised:
                self.store()
            self.assertEqual(raised.exception.status, 422)
            run.assert_called_once()

    def test_foreign_forged_task_and_symlink_reads_fail_without_filesystem_paths(self):
        refs = self.store()
        self.commit(refs)
        foreign = "other-image-project"
        self.register(foreign)
        for project, image_id in ((foreign, refs[0]["id"]), (self.project, "../../secret")):
            with self.assertRaises(images.ImageError) as error:
                images.read(project, image_id)
            self.assertNotIn(str(config.ROOT), str(error.exception))
        with self.assertRaises(images.ImageError):
            images.resolve(self.project, [{**refs[0], "source_message_id": "forged"}])
        with self.assertRaises(images.ImageError):
            images.resolve(self.project, refs, task="unassigned")
        assigned = S.tasks_dir(self.project) / "assigned"
        S.write_json(assigned / "status.json", {"slug": "assigned", "images": refs})
        self.assertTrue(images.resolve(self.project, refs, task="assigned"))
        image_path = Path(images.resolve(self.project, refs)[0]["path"])
        image_path.unlink()
        image_path.symlink_to(self.tmp / "private-secret")
        with self.assertRaises(images.ImageError):
            images.read(self.project, refs[0]["id"])

    def test_integrity_failure_missing_and_denied_are_explicit(self):
        refs = self.store()
        self.commit(refs)
        image_path = Path(images.resolve(self.project, refs)[0]["path"])
        image_path.write_bytes(b"replacement")
        with self.assertRaisesRegex(images.ImageError, "damaged"):
            images.read(self.project, refs[0]["id"])
        with mock.patch.object(images.os, "open", side_effect=PermissionError("private path")):
            with self.assertRaises(images.ImageError) as denied:
                images.read(self.project, refs[0]["id"])
            self.assertEqual(denied.exception.status, 403)
            self.assertNotIn("private path", str(denied.exception))
        image_path.unlink()
        with self.assertRaises(images.ImageError) as missing:
            images.read(self.project, refs[0]["id"])
        self.assertEqual(missing.exception.status, 404)

    def test_special_saved_files_refuse_without_blocking_the_project(self):
        for filename in ("image.png", "metadata.json"):
            with self.subTest(filename=filename):
                refs = self.store()
                directory = config.project_dir(self.project) / "images" / refs[0]["id"]
                path = directory / filename
                path.unlink()
                os.mkfifo(path)
                result = subprocess.run([sys.executable, "-c", '''
from tests.support import AltitudeCase
from altitude import images
from pathlib import Path
from unittest import mock
import sys
with mock.patch.object(images, "_root", return_value=Path(sys.argv[1])):
    try:
        images._load(sys.argv[2], sys.argv[3])
    except images.ImageError as exc:
        assert exc.status == 403, exc
    else:
        raise AssertionError("special image file was accepted")
''', str(directory.parent), self.project, refs[0]["id"]], capture_output=True, text=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_queue_removal_orphan_sweep_and_archived_assignment_retention(self):
        old, queued, archived, fresh = (self.store()[0] for _ in range(4))
        root = config.project_dir(self.project)
        self.commit([queued], path=root / "l3-queue.jsonl")
        task = S.tasks_dir(self.project) / "assignment"
        S.write_json(task / "status.json", {"slug": "assignment", "image_messages": [{"images": [archived]}]})
        archive = S.archive_dir(self.project)
        archive.mkdir()
        shutil.move(task, archive / "assignment")
        for ref in (old, queued, archived):
            os.utime(root / "images" / ref["id"], (time.time() - 90000,) * 2)
        staging = root / "images" / ".stage-interrupted"
        staging.mkdir()
        os.utime(staging, (time.time() - 90000,) * 2)
        self.assertEqual(images.collect(self.project), 2)
        self.assertFalse((root / "images" / old["id"]).exists())
        self.assertTrue((root / "images" / fresh["id"]).exists())
        self.assertTrue(images.read(self.project, archived["id"])[0])
        self.assertTrue(images.resolve(self.project, [archived], task="assignment"))
        (root / "l3-queue.jsonl").unlink()
        self.assertEqual(images.collect(self.project), 1)
        self.assertTrue((root / "images" / archived["id"]).exists())

    def test_corrupt_reference_store_stops_cleanup_without_deleting_history(self):
        ref = self.store()[0]
        directory = config.project_dir(self.project) / "images" / ref["id"]
        os.utime(directory, (time.time() - 90000,) * 2)
        S.atomic_write(config.project_dir(self.project) / "chat.jsonl", "corrupt\n")
        with self.assertRaises(images.ImageError):
            images.collect(self.project)
        self.assertTrue(directory.exists())

    def test_detach_and_reattach_keeps_the_same_durable_image(self):
        refs = self.store()
        self.commit(refs)
        projects = config.load_projects()
        projects.pop(self.project)
        config.save_projects(projects)
        with self.assertRaises(images.ImageError):
            images.read(self.project, refs[0]["id"])
        self.register(self.project)
        self.assertEqual(images.read(self.project, refs[0]["id"])[1], refs[0])
