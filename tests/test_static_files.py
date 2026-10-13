"""Website reads bind successful responses, compression and cache hits to verified files."""
import gzip
import io
import importlib.util
import os
import sys
import unittest
from collections import Counter
from contextlib import contextmanager, nullcontext
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from unittest import mock

from tests.support import AltitudeCase, REPO
from altitude import config, platform, server, tasks


class StaticFiles(AltitudeCase):
    def setUp(self):
        super().setUp()
        self.dist = self.tmp / "dist"
        self.dist.mkdir()
        self.index = self.dist / "index.html"
        self.index.write_bytes(b"<title>fictional build</title>")
        self.assets = self.dist / "assets"
        self.assets.mkdir()
        self.asset = self.assets / "app.js"
        self.asset.write_bytes(b"console.log('fictional build');")
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        (self.outside / "app.js").write_bytes(b"outside bytes")
        self.patch(config, "WEB_DIST", self.dist)
        server._gzip_cache.clear()
        self.addCleanup(server._gzip_cache.clear)

    def request(self, path, encoded=False):
        handler = server.Handler.__new__(server.Handler)
        handler.headers = {"Accept-Encoding": "gzip"} if encoded else {}
        handler.wfile = io.BytesIO()
        status, headers = [], {}
        handler.send_response = status.append
        handler.send_header = headers.__setitem__
        handler.end_headers = lambda: None
        handler._static(path)
        data = handler.wfile.getvalue()
        if headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return status[0], headers, data

    @contextmanager
    def no_forbidden_lookups(self, *extra):
        """A forbidden attempt raises AssertionError, outside the handler's caught errors."""
        forbidden = (self.outside, *extra)
        originals = {name: getattr(os, name) for name in ("open", "stat", "lstat", "readlink", "dup")}
        directories = {}
        def candidate(name, kwargs):
            if isinstance(name, int):
                return None
            parent = directories.get(kwargs.get("dir_fd"))
            return os.path.abspath(os.path.join(parent or os.getcwd(), os.fspath(name)))
        def checked(name, kwargs):
            path = candidate(name, kwargs)
            if path is not None:
                for root in forbidden:
                    self.assertFalse(path == str(root) or path.startswith(str(root) + "/"),
                                     "forbidden metadata/open attempt: " + path)
            return path
        def opening(name, flags, *args, **kwargs):
            path = checked(name, kwargs)
            fd = originals["open"](name, flags, *args, **kwargs)
            if flags & os.O_DIRECTORY:
                directories[fd] = path
            return fd
        def duplicate(fd):
            result = originals["dup"](fd)
            if fd in directories:
                directories[result] = directories[fd]
            return result
        def observed(operation):
            def call(name, *args, **kwargs):
                checked(name, kwargs)
                return originals[operation](name, *args, **kwargs)
            return call
        with mock.patch.object(os, "open", side_effect=opening), \
             mock.patch.object(os, "dup", side_effect=duplicate), \
             mock.patch.object(os, "stat", side_effect=observed("stat")), \
             mock.patch.object(os, "lstat", side_effect=observed("lstat")), \
             mock.patch.object(os, "readlink", side_effect=observed("readlink")):
            yield

    def test_metadata_refusal_before_lookup_for_traversal_links_and_private_targets(self):
        private = self.dist / "sealed"
        private.mkdir()
        (private / "marker.js").write_bytes(b"fictional private marker")
        (self.dist / "outside-link").symlink_to(self.outside / "app.js")
        (self.dist / "private-link").symlink_to(private / "marker.js")
        (self.dist / "detour").symlink_to("../outside/../dist/assets/app.js")
        with mock.patch.object(platform, "private_places", return_value=(str(private),)), \
             self.no_forbidden_lookups(private):
            for path in ("/../outside/app.js", "/%2e%2e/outside/app.js", "/outside-link",
                         "/private-link", "/sealed/../assets/app.js", "/detour"):
                for encoded in (False, True):
                    self.assertEqual(self.request(path, encoded)[0], 404)
            self.assertEqual(self.request("/assets/app.js", True)[2], b"console.log('fictional build');")
            self.index.unlink()
            self.index.symlink_to(private / "marker.js")
            self.assertEqual(self.request("/projects/atlas")[0], 404)

    def test_configured_aliases_and_external_alias_restriction(self):
        configured = self.tmp / "configured-build"
        configured.symlink_to(self.dist, target_is_directory=True)
        external = self.tmp / "external-build"
        external.symlink_to(self.dist, target_is_directory=True)
        (self.dist / "configured.js").symlink_to(configured / "assets/app.js")
        (self.dist / "external.js").symlink_to(external / "assets/app.js")
        with mock.patch.object(config, "WEB_DIST", configured), self.no_forbidden_lookups(external):
            for path in ("/", "/configured.js", "/assets/app.js"):
                self.assertEqual(self.request(path, True)[0], 200)
            self.assertEqual(self.request("/external.js")[0], 404)
        configured.unlink()
        configured.symlink_to(self.outside, target_is_directory=True)
        with mock.patch.object(config, "WEB_DIST", configured), \
             mock.patch.object(platform, "private_places", return_value=(str(self.outside),)), \
             self.no_forbidden_lookups():
            self.assertEqual(self.request("/")[0], 404)
        with mock.patch.object(config, "WEB_DIST", external), \
             mock.patch.object(platform, "private_places", return_value=(str(external),)), \
             self.no_forbidden_lookups(external):
            self.assertEqual(self.request("/")[0], 404)

    def test_missing_classification_follows_resolved_components_and_virtual_parents(self):
        (self.dist / "to-assets").symlink_to("assets", target_is_directory=True)
        (self.assets / "away").symlink_to("../client-route")
        for path, status in (("/to-assets/missing.js", 404), ("/assets/away/missing", 200),
                             ("/missing/../assets/missing.js", 404), ("/index.html/../assets/app.js", 200),
                             ("/index.html/route", 200)):
            self.assertEqual(self.request(path)[0], status, path)

    def test_index_absence_is_unavailable_but_unsafe_index_is_refused(self):
        self.index.unlink()
        self.assertEqual(self.request("/")[0], 503)
        self.index.mkdir()
        self.assertEqual(self.request("/")[0], 503)
        self.index.rmdir()
        self.index.symlink_to("absent/route")
        self.assertEqual(self.request("/")[0], 503)
        self.index.unlink()
        self.index.symlink_to(self.outside / "app.js")
        with self.no_forbidden_lookups():
            self.assertEqual(self.request("/projects/atlas")[0], 404)
        self.index.unlink()
        self.index.symlink_to("index.html")
        self.assertEqual(self.request("/")[0], 404)

    def test_inspected_file_removal_never_becomes_successful_fallback(self):
        ordinary = self.dist / "direct.txt"
        ordinary.write_bytes(b"direct bytes")
        original = os.open
        for leaf, path in ((ordinary, "/direct.txt"), (self.index, "/")):
            expected = leaf.read_bytes()
            def opening(name, flags, *args, **kwargs):
                if name == leaf.name:
                    leaf.unlink()
                return original(name, flags, *args, **kwargs)
            try:
                with mock.patch.object(os, "open", side_effect=opening):
                    self.assertEqual(self.request(path)[0], 404)
            finally:
                leaf.write_bytes(expected)

    def test_acyclic_64_links_completed_visits_and_contextual_hardlinks(self):
        for n in reversed(range(64)):
            (self.dist / f"chain{n}").symlink_to(f"chain{n + 1}" if n < 63 else "assets/app.js")
        (self.dist / "alias").symlink_to("assets", target_is_directory=True)
        for path in ("/chain0", "/alias/../alias/app.js"):
            self.assertEqual(self.request(path, True), self.request("/assets/app.js", True))
        for n in reversed(range(64)):
            (self.tmp / f"build{n}").symlink_to(f"build{n + 1}" if n < 63 else "dist",
                                              target_is_directory=True)
        with mock.patch.object(config, "WEB_DIST", self.tmp / "build0"):
            self.assertEqual(self.request("/assets/app.js", True), self.request("/chain0", True))
        self.index.unlink()
        self.index.symlink_to("chain0")
        status, headers, data = self.request("/", True)
        self.assertEqual((status, data), (200, b"console.log('fictional build');"))
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Content-Encoding", headers)
        # These symlinks share an inode but have different parent/name contexts.
        (self.dist / "a").mkdir()
        (self.dist / "a/a").mkdir()
        (self.dist / "a/a/a").write_bytes(b"finite hardlinked links")
        link = self.dist / "step"
        link.symlink_to("a/step")
        os.link(link, self.dist / "a/step", follow_symlinks=False)
        self.assertEqual(link.lstat().st_ino, (self.dist / "a/step").lstat().st_ino)
        (self.dist / "a/a/step").symlink_to("a")
        self.assertEqual(self.request("/step")[2], b"finite hardlinked links")
        for name, target in (("self", "self"), ("left", "right"), ("right", "left"),
                             ("expanding", "expanding/more")):
            (self.dist / name).symlink_to(target)
        for path in ("/self", "/left", "/expanding"):
            self.assertEqual(self.request(path)[0], 404)

    def test_successful_bytes_headers_and_inside_links(self):
        unusual = self.assets / "a \\ β%.js"
        unusual.write_bytes(b"unusual name")
        (self.dist / "alias.js").symlink_to(self.asset)
        (self.dist / "nested").symlink_to(self.assets, target_is_directory=True)
        for path, target, ctype, immutable in (
            ("/", self.index, "text/html; charset=utf-8", False),
            ("/projects/atlas", self.index, "text/html; charset=utf-8", False),
            ("/index.html", self.index, "text/html; charset=utf-8", False),
            ("/assets/app.js", self.asset, "text/javascript", True),
            ("/alias.js", self.asset, "text/javascript", True),
            ("/nested/app.js", self.asset, "text/javascript", True),
            ("/assets/" + quote(unusual.name, safe=""), unusual, "text/javascript", True),
        ):
            for encoded in (False, True):
                with self.subTest(path=path, encoded=encoded):
                    status, headers, data = self.request(path, encoded)
                    self.assertEqual(status, 200)
                    self.assertEqual(data, target.read_bytes())
                    self.assertEqual(headers["Content-Type"], ctype)
                    self.assertEqual(headers["Cache-Control"],
                                     "public, max-age=31536000, immutable" if immutable else "no-store")
                    self.assertEqual(headers.get("Vary"), "Accept-Encoding" if immutable else None)
                    self.assertEqual(headers.get("Content-Encoding"), "gzip" if immutable and encoded else None)
                    self.assertEqual(headers["Content-Security-Policy"], "frame-ancestors 'none'")

    def test_branching_acyclic_links_reuse_verified_addresses_with_bounded_work(self):
        (self.dist / "l0").symlink_to("assets", target_is_directory=True)
        depth = 8
        for n in range(1, depth + 1):
            (self.dist / f"l{n}").symlink_to(f"l{n - 1}/../l{n - 1}", target_is_directory=True)
        expected = self.request("/assets/app.js", True)
        with self.no_forbidden_lookups(), \
             mock.patch.object(os, "stat", wraps=os.stat) as observed, \
             mock.patch.object(os, "readlink", wraps=os.readlink) as links:
            self.assertEqual(self.request(f"/l{depth}/app.js", True), expected)
        # Each alias text is expanded once; completed visits recheck verified addresses.
        self.assertEqual(links.call_count, depth + 1)
        self.assertLess(observed.call_count, 12 * (depth + 1))

    def test_reused_addresses_refuse_changed_links_and_target_nodes(self):
        alias = self.dist / "alias"
        alias.symlink_to("assets", target_is_directory=True)
        original = os.stat
        for change in ("alias", "directory"):
            for encoded in (False, True):
                seen = []
                replacement_call = 3 if change == "alias" else 2
                saved = self.dist / "saved-assets"
                def observed(name, *args, **kwargs):
                    if name == change.replace("directory", "assets"):
                        seen.append(name)
                        if len(seen) == replacement_call:
                            if change == "alias":
                                alias.unlink()
                                alias.symlink_to(self.outside, target_is_directory=True)
                            else:
                                self.assets.rename(saved)
                                self.assets.mkdir()
                                (self.assets / "app.js").write_bytes(b"changed target")
                    return original(name, *args, **kwargs)
                try:
                    with mock.patch.object(os, "stat", side_effect=observed), self.no_forbidden_lookups():
                        self.assertEqual(self.request("/alias/../alias/app.js", encoded)[0], 404)
                    self.assertEqual(len(seen), replacement_call)
                finally:
                    if change == "alias":
                        alias.unlink()
                        alias.symlink_to("assets", target_is_directory=True)
                    else:
                        (self.assets / "app.js").unlink()
                        self.assets.rmdir()
                        saved.rename(self.assets)

    def test_index_links_keep_root_and_spa_html_and_direct_target_classification(self):
        self.index.unlink()
        self.index.symlink_to(self.asset)
        for path in ("/", "/projects/atlas"):
            status, headers, data = self.request(path, True)
            self.assertEqual((status, data), (200, self.asset.read_bytes()))
            self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertNotIn("Content-Encoding", headers)
            self.assertNotIn("Vary", headers)
        status, headers, data = self.request("/index.html", True)
        self.assertEqual((status, data), (200, self.asset.read_bytes()))
        self.assertEqual(headers["Content-Type"], "text/javascript")
        self.assertEqual(headers["Content-Encoding"], "gzip")

    def test_missing_build_asset_and_invalid_paths(self):
        (self.assets / "escape.js").symlink_to(self.outside / "app.js")
        for path in ("/assets/missing.js", "/../outside/app.js", "/%2e%2e/outside/app.js",
                     "/assets/escape.js", "/assets/app.js%00"):
            for encoded in (False, True):
                with self.subTest(path=path, encoded=encoded):
                    self.assertEqual(self.request(path, encoded)[0], 404)
        self.index.unlink()
        for path in ("/", "/assets/app.js", "/projects/atlas"):
            self.assertEqual(self.request(path)[0], 503)
        self.index.mkdir()
        self.assertEqual(self.request("/")[0], 503)

    def test_nonasset_routes_beneath_ordinary_files_keep_the_spa_fallback(self):
        (self.dist / "ordinary.txt").write_bytes(b"ordinary file")
        for path in ("/index.html/route", "/ordinary.txt/nested/route"):
            status, headers, data = self.request(path, True)
            self.assertEqual((status, data), (200, self.index.read_bytes()))
            self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(self.request("/assets/app.js/route", True)[0], 404)

    def test_nonasset_replacement_link_does_not_become_a_successful_spa_route(self):
        folder = self.dist / "routes"
        folder.mkdir()
        original = os.open
        for encoded in (False, True):
            swapped = []
            saved = self.dist / "saved-routes"
            def opened(name, flags, *args, **kwargs):
                if name == "routes" and not swapped:
                    folder.rename(saved)
                    folder.symlink_to(self.outside, target_is_directory=True)
                    swapped.append(True)
                return original(name, flags, *args, **kwargs)
            try:
                with mock.patch.object(os, "open", side_effect=opened), self.no_forbidden_lookups():
                    self.assertEqual(self.request("/routes/missing", encoded)[0], 404)
            finally:
                if swapped:
                    folder.unlink()
                    saved.rename(folder)

    def test_outside_index_links_are_unavailable_for_every_route(self):
        self.index.unlink()
        self.index.symlink_to(self.outside / "app.js")
        for path in ("/", "/projects/atlas", "/index.html", "/assets/app.js"):
            self.assertEqual(self.request(path)[0], 404)

    def test_replaced_file_or_directory_cannot_redirect_cold_or_warm_reads(self):
        original = os.open
        for component in ("app.js", "assets"):
            for encoded in (False, True):
                for warm in (False, True):
                    with self.subTest(component=component, encoded=encoded, warm=warm):
                        if warm:
                            self.assertEqual(self.request("/assets/app.js", True)[0], 200)
                        swapped = []
                        target = self.asset if component == "app.js" else self.assets
                        saved = target.with_name(target.name + ".saved")
                        def opened(path, flags, *args, **kwargs):
                            if path == component and "dir_fd" in kwargs and not swapped:
                                target.rename(saved)
                                target.symlink_to(self.outside / component if component == "app.js" else self.outside,
                                                  target_is_directory=component == "assets")
                                swapped.append(True)
                            return original(path, flags, *args, **kwargs)
                        try:
                            with mock.patch.object(os, "open", side_effect=opened), self.no_forbidden_lookups():
                                status, _, data = self.request("/assets/app.js", encoded)
                            self.assertTrue(swapped)
                            self.assertEqual(status, 404)
                            self.assertNotIn(b"outside bytes", data)
                        finally:
                            if swapped:
                                target.unlink()
                                saved.rename(target)
                        server._gzip_cache.clear()

    def test_replaced_index_fails_and_open_asset_survives_a_later_rename(self):
        original_open, original_gzip = os.open, server._gzipped
        for path in ("/", "/projects/atlas"):
            def opened(name, flags, *args, **kwargs):
                if name == "index.html":
                    self.index.unlink()
                    self.index.symlink_to(self.outside / "app.js")
                return original_open(name, flags, *args, **kwargs)
            try:
                with mock.patch.object(os, "open", side_effect=opened), self.no_forbidden_lookups():
                    self.assertEqual(self.request(path)[0], 404)
            finally:
                self.index.unlink()
                self.index.write_bytes(b"<title>fictional build</title>")
        expected = self.asset.read_bytes()
        def compressed(path, stream):
            self.asset.unlink()
            self.asset.symlink_to(self.outside / "app.js")
            return original_gzip(path, stream)
        with mock.patch.object(server, "_gzipped", side_effect=compressed):
            status, _, data = self.request("/assets/app.js", True)
        self.assertEqual((status, data), (200, expected))
        self.assertEqual(self.request("/assets/app.js", True)[0], 404)

    def test_cache_reuses_encoding_but_invalidates_replacement_and_in_place_changes(self):
        original = server.gzip.compress
        with mock.patch.object(server.gzip, "compress", wraps=original) as compress:
            for _ in range(2):
                self.assertEqual(self.request("/assets/app.js", True)[2], self.asset.read_bytes())
            self.assertEqual(compress.call_count, 1)
            replacement = self.assets / "replacement.js"
            replacement.write_bytes(b"replacement bytes")
            replacement.replace(self.asset)
            self.assertEqual(self.request("/assets/app.js", True)[2], b"replacement bytes")
            self.asset.write_bytes(b"another revision")
            self.assertEqual(self.request("/assets/app.js", True)[2], b"another revision")
            self.assertEqual(compress.call_count, 3)
        for n in range(70):
            (self.assets / f"build{n}.js").write_bytes(str(n).encode())
            self.assertEqual(self.request(f"/assets/build{n}.js", True)[0], 200)
        self.assertEqual(len(server._gzip_cache), 64)
        self.assertNotIn(self.asset, server._gzip_cache)

    def test_pinned_build_root_serves_original_namespace_after_replacement(self):
        original = os.open
        (self.outside / "index.html").write_bytes(b"outside index")
        for path in ("/", "/projects/atlas", "/assets/app.js"):
            for encoded in (False, True):
                for warm in (False, True):
                    with self.subTest(path=path, encoded=encoded, warm=warm):
                        baseline = self.request(path, encoded)
                        server._gzip_cache.clear()
                        if warm:
                            self.request("/assets/app.js", True)
                        swapped = []
                        saved = self.dist.with_name("saved-dist")
                        def opened(name, flags, *args, **kwargs):
                            if name == "index.html" and not swapped:
                                self.dist.rename(saved)
                                self.dist.symlink_to(self.outside, target_is_directory=True)
                                swapped.append(True)
                            return original(name, flags, *args, **kwargs)
                        try:
                            with mock.patch.object(os, "open", side_effect=opened), self.no_forbidden_lookups():
                                response = self.request(path, encoded)
                            status, _, data = response
                            expected = b"console.log('fictional build');" if path.startswith("/assets/") else b"<title>fictional build</title>"
                            self.assertEqual((status, data), (200, expected))
                            self.assertEqual(response, baseline)
                            self.assertNotIn(b"outside", data)
                        finally:
                            if swapped:
                                self.dist.unlink()
                                saved.rename(self.dist)
                        server._gzip_cache.clear()

    def test_concurrent_cold_and_warm_compression_returns_verified_bytes(self):
        expected = self.asset.read_bytes()
        for warm in (False, True):
            if warm:
                self.request("/assets/app.js", True)
            with ThreadPoolExecutor(max_workers=4) as workers:
                results = list(workers.map(lambda _: self.request("/assets/app.js", True), range(16)))
            self.assertTrue(all(status == 200 and data == expected for status, _, data in results))
            self.assertEqual(len(server._gzip_cache), 1)

    def test_changed_open_file_signature_during_compression_is_not_cached_or_served(self):
        original = server.gzip.compress
        sibling = self.tmp / "snapshot.js"
        def compressed(data, **kwargs):
            os.link(self.asset, sibling)
            return original(data, **kwargs)
        with mock.patch.object(server.gzip, "compress", side_effect=compressed):
            status, _, data = self.request("/assets/app.js", True)
        self.assertEqual(status, 404)
        self.assertNotIn(b"console.log", data)
        self.assertNotIn(self.asset, server._gzip_cache)

    def test_document_build_copies_do_not_change_original_signature_during_gzip(self):
        spec = importlib.util.spec_from_file_location("document_update_fixture", REPO / "web/e2e/document-update-service.py")
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "path", [str(REPO / "web/e2e"), *sys.path]):
            spec.loader.exec_module(module)
        expected = self.asset.read_bytes()
        def signature():
            info = self.asset.stat()
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink
        before = signature()
        snapshots = []
        original = server.gzip.compress
        def compressed(data, **kwargs):
            for _ in range(2):
                snapshot = module.copy_build(self.dist)
                snapshots.append(snapshot)
                copied = snapshot / "assets/app.js"
                self.assertNotEqual((copied.stat().st_dev, copied.stat().st_ino), before[:2])
                self.assertEqual(copied.read_bytes(), expected)
                copied.unlink()
                copied.write_bytes(b"fictional updated build")
                self.assertEqual(signature(), before)
                self.assertEqual(self.asset.read_bytes(), expected)
            return original(data, **kwargs)
        with mock.patch.object(server.gzip, "compress", side_effect=compressed):
            status, _, data = self.request("/assets/app.js", True)
        self.assertEqual((status, data), (200, expected))
        self.assertEqual(signature(), before)
        self.assertIn(self.asset, server._gzip_cache)
        self.assertNotEqual((snapshots[0] / "index.html").stat().st_ino,
                            (snapshots[1] / "index.html").stat().st_ino)

    def test_nonregular_substitution_and_read_failures_close_all_descriptors(self):
        original_open, original_dup, original_close, original_stat = os.open, os.dup, os.close, os.fstat
        for failure in ("fifo", "initial-stat", "directory-stat", "stat", "fdopen", "read", "close"):
            opened, closed = [], []
            def opening(path, flags, *args, **kwargs):
                if path == "app.js" and failure == "fifo":
                    self.asset.unlink()
                    os.mkfifo(self.asset)
                fd = original_open(path, flags, *args, **kwargs)
                opened.append((fd, path))
                return fd
            def duplicating(fd):
                duplicate = original_dup(fd)
                opened.append((duplicate, "duplicate"))
                return duplicate
            def closing(fd):
                original_close(fd)
                closed.append(fd)
                if failure == "close" and any(item == (fd, "app.js") for item in opened):
                    raise OSError("fictional close failure")
            def observed(fd):
                if failure == "initial-stat" and len(opened) == 2:
                    raise OSError("fictional initial stat failure")
                if failure == "directory-stat" and opened[-1] == (fd, "assets"):
                    raise OSError("fictional directory stat failure")
                if failure == "stat" and opened[-1] == (fd, "app.js"):
                    raise OSError("fictional stat failure")
                return original_stat(fd)
            try:
                with mock.patch.object(os, "open", side_effect=opening), \
                     mock.patch.object(os, "dup", side_effect=duplicating), \
                     mock.patch.object(os, "close", side_effect=closing), \
                     mock.patch.object(os, "fstat", side_effect=observed), \
                     (mock.patch.object(os, "fdopen", side_effect=OSError("fictional stream failure"))
                      if failure == "fdopen" else nullcontext()), \
                     mock.patch.object(server, "_gzipped", side_effect=OSError("fictional read failure")) \
                         if failure == "read" else nullcontext():
                    self.assertEqual(self.request("/assets/app.js", True)[0], 404)
                self.assertEqual(Counter(fd for fd, _ in opened), Counter(closed))
                for fd, _ in opened:
                    with self.assertRaises(OSError):
                        original_stat(fd)
            finally:
                if failure == "fifo":
                    self.asset.unlink()
                    self.asset.write_bytes(b"regular again")

    @unittest.skipUnless(sys.platform == "linux", "requires Linux kernel directory search permissions")
    def test_unprivileged_linux_can_read_beneath_execute_only_directories(self):
        self.assertNotEqual(os.geteuid(), 0, "root cannot establish execute-only-directory parity")
        self.dist.chmod(0o111)
        self.assets.chmod(0o111)
        try:
            with self.assertRaises(PermissionError):
                os.listdir(self.assets)
            for encoded in (False, True):
                status, _, data = self.request("/assets/app.js", encoded)
                self.assertEqual((status, data), (200, b"console.log('fictional build');"))
            # The sole existing search-mode caller also keeps lookup without directory listing.
            with tasks._directory(self.dist, ["assets"], search=True) as directory:
                fd = os.open("app.js", os.O_RDONLY, dir_fd=directory)
                try:
                    self.assertEqual(os.read(fd, 4096), b"console.log('fictional build');")
                finally:
                    os.close(fd)
        finally:
            self.assets.chmod(0o755)
            self.dist.chmod(0o755)
