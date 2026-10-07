import io
import json
import os
import struct
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
from tests.support import container_namespace_metadata

from altitude import container_archive as backup


@unittest.skipUnless(hasattr(os, "listxattr"), "the archive runs in the Linux image, which keeps Linux extended attributes (macOS containers: #643)")
class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enterContext(container_namespace_metadata(self.root))
        self.enterContext(mock.patch.object(backup.os, 'chown'))
        self.home, self.projects = self.root/'home', self.root/'projects'
        self.home.mkdir(); self.projects.mkdir()
        (self.home/'Projects').mkdir()
        (self.home/'private').write_bytes(b'fictional-secret\x00\xff')
        (self.home/'private').chmod(0o600)
        os.utime(self.home/'private', ns=(1700000000123456789, 1700000000987654321))
        (self.projects/'repo').mkdir()
        (self.projects/'repo/file').write_bytes(b'project\x00contents')
        (self.projects/'repo/link').symlink_to('/outside/fictional')
        os.link(self.projects/'repo/file', self.projects/'repo/hard')

    def output(self):
        destination = self.root/'restored'
        destination.mkdir()
        home, projects = destination/'home', destination/'projects'
        home.mkdir(); projects.mkdir()
        return home, projects

    def archive(self):
        stream = io.BytesIO()
        result = backup.export(stream, self.home, self.projects)
        stream.seek(0)
        return stream, result

    def altered(self, change):
        original, _ = self.archive()
        output = io.BytesIO()
        with tarfile.open(fileobj=original) as source, tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as target:
            for member in source:
                data = source.extractfile(member).read() if member.isreg() else b''
                member, data = change(member, data)
                if member is not None:
                    target.addfile(member, io.BytesIO(data))
        output.seek(0)
        return output

    def test_round_trip_private_metadata_links_and_two_volumes(self):
        os.setxattr(self.home/'private', 'user.fixture', b'private-metadata')
        stream, expected = self.archive()
        home, projects = self.output()
        self.assertEqual(backup.restore(stream, home, projects), expected)
        self.assertEqual((home/'private').read_bytes(), b'fictional-secret\x00\xff')
        self.assertEqual((home/'private').stat().st_mode & 0o777, 0o600)
        self.assertEqual((home/'private').stat().st_mtime_ns, 1700000000987654321)
        self.assertEqual(os.getxattr(home/'private', 'user.fixture'), b'private-metadata')
        self.assertEqual((projects/'repo/link').readlink(), Path('/outside/fictional'))
        self.assertEqual((projects/'repo/file').stat().st_ino, (projects/'repo/hard').stat().st_ino)
        self.assertEqual(list((home/'Projects').iterdir()), [])

    def test_marker_is_not_inherited(self):
        (self.home/backup.MARKER).write_text('fictional previous completion')
        stream, _ = self.archive()
        home, projects = self.output()
        backup.restore(stream, home, projects)
        self.assertFalse((home/backup.MARKER).exists())

    def test_nested_marker_and_non_utf8_names_round_trip(self):
        (self.projects/'repo'/backup.MARKER).write_text('ordinary project file')
        odd=self.projects/'repo'/os.fsdecode(b'name-\xff')
        odd.write_bytes(b'non-UTF8 filename')
        (self.projects/'repo/nonutf-link').symlink_to(os.fsdecode(b'target-\xfe'))
        stream,expected=self.archive()
        self.assertEqual(backup.restore(stream),expected)  # non-writing completion validation
        stream.seek(0)
        home,projects=self.output()
        self.assertEqual(backup.restore(stream,home,projects),expected)
        self.assertEqual((projects/'repo'/backup.MARKER).read_text(),'ordinary project file')
        self.assertEqual((projects/'repo'/odd.name).read_bytes(),b'non-UTF8 filename')
        self.assertEqual(os.fsencode(os.readlink(projects/'repo/nonutf-link')),b'target-\xfe')

    def test_nonwriting_validation_refuses_truncated_transport(self):
        stream=self.altered(lambda m,d:(None,d) if m.name==backup.END else (m,d))
        with self.assertRaisesRegex(ValueError,'incomplete'): backup.restore(stream)

    def test_hidden_project_data_refused(self):
        (self.home/'Projects/hidden').write_text('must not disappear')
        with self.assertRaisesRegex(ValueError, 'hidden Projects'):
            self.archive()

    def test_export_uses_restore_path_policy_before_reading_regular_mountpoint(self):
        (self.home/'Projects').rmdir()
        (self.home/'Projects').write_bytes(b'data that must not be read')
        with mock.patch.object(backup.os,'open',side_effect=AssertionError('read invalid source')):
            with self.assertRaisesRegex(backup.PolicyError,'empty directory'): self.archive()

    def test_export_refuses_depth_before_python_recursion_limit(self):
        folder=self.projects
        for _ in range(129):
            folder=folder/'d'; folder.mkdir()
        with self.assertRaisesRegex(backup.PolicyError,'128 components'): self.archive()

    def test_acl_named_ids_obey_same_policy_on_export_and_restore(self):
        def acl(identity):
            return struct.pack('<I',2)+b''.join(struct.pack('<HHI',*entry) for entry in
                [(1,6,0xffffffff),(2,4,identity),(4,0,0xffffffff),(16,0,0xffffffff),(32,0,0xffffffff)])
        # This worker namespace cannot create an ACL for an unmapped ID. Feed
        # the kernel observation at that seam; export must reject before reading.
        with mock.patch.object(backup.os,'listxattr',return_value=['system.posix_acl_access']), \
             mock.patch.object(backup.os,'getxattr',return_value=acl(1001)):
            with self.assertRaisesRegex(backup.PolicyError,'namespace IDs'): self.archive()
        os.setxattr(self.home/'private','system.posix_acl_access',acl(1000))
        stream,_=self.archive(); home,projects=self.output()
        backup.restore(stream,home,projects)
        self.assertEqual(os.getxattr(home/'private','system.posix_acl_access'),acl(1000))
        def change(member,data):
            if member.name=='home/private':
                import base64
                member.pax_headers[backup.XATTR]=json.dumps({'system.posix_acl_access':base64.b64encode(acl(65534)).decode()})
            return member,data
        with self.assertRaisesRegex(backup.PolicyError,'namespace IDs'):
            backup.restore(self.altered(change))

    def test_policy_categories_survive_content_free_helper_exit(self):
        for code in range(80,86):
            with mock.patch.object(backup,'helper',side_effect=backup.PolicyError(code,'private filename')):
                with self.assertRaises(SystemExit) as caught: backup.entrypoint('export',{})
                self.assertEqual(caught.exception.code,code)

    def test_setgid_refused(self):
        (self.projects/'repo').chmod(0o2755)
        with self.assertRaisesRegex(ValueError, 'Set-ID'):
            self.archive()

    def test_fifo_refused(self):
        os.mkfifo(self.home/'fifo')
        with self.assertRaisesRegex(ValueError, 'file type'):
            self.archive()

    def test_entry_and_data_bounds(self):
        for options in ({'max_entries': 1}, {'max_bytes': 1}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'limit'):
                backup.export(io.BytesIO(), self.home, self.projects, **options)

    def test_missing_completion_even_after_successful_eof(self):
        stream = self.altered(lambda m, d: (None, d) if m.name == backup.END else (m, d))
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            backup.restore(stream, *self.output())

    def test_same_size_corruption(self):
        stream = self.altered(lambda m, d: (m, b'X'*len(d)) if m.name == 'home/private' else (m, d))
        with self.assertRaisesRegex(ValueError, 'digest'):
            backup.restore(stream, *self.output())

    def test_malicious_paths_nodes_metadata_and_links(self):
        cases = [
            ('name', '../outside'), ('name', '/outside'), ('name', 'home/../outside'),
            ('name', 'home//private'), ('name', 'home/./private'),
            ('name', 'home/Projects/hidden'), ('name', 'home/'+backup.MARKER),
            ('uid', 1001), ('mode', 0o4755), ('type', tarfile.FIFOTYPE),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                def change(m, d):
                    if m.name == 'home/private': setattr(m, key, value)
                    return m, d
                stream = self.altered(change)
                with tempfile.TemporaryDirectory(dir=self.root) as output:
                    home, projects = Path(output)/'home', Path(output)/'projects'
                    home.mkdir(); projects.mkdir()
                    with self.assertRaises(ValueError): backup.restore(stream, home, projects)

    def test_cross_volume_hardlink_refused(self):
        def change(m, d):
            if m.islnk(): m.linkname = 'home/private'
            return m, d
        with self.assertRaisesRegex(ValueError, 'same volume'):
            backup.restore(self.altered(change), *self.output())

    def test_symlink_parent_never_followed(self):
        sentinel = self.root/'sentinel'
        sentinel.write_text('unchanged')
        def change(m, d):
            if m.name == 'projects/repo':
                m.type, m.linkname = tarfile.SYMTYPE, str(self.root)
            return m, d
        with self.assertRaisesRegex(ValueError, 'earlier directory'):
            backup.restore(self.altered(change), *self.output())
        self.assertEqual(sentinel.read_text(), 'unchanged')

    def test_nonempty_target_refused(self):
        stream, _ = self.archive()
        home, projects = self.output()
        (home/'preserve').write_text('untouched')
        with self.assertRaisesRegex(ValueError, 'empty'):
            backup.restore(stream, home, projects)
        self.assertEqual((home/'preserve').read_text(), 'untouched')

    def test_oversized_pax_before_parser_allocation(self):
        header = tarfile.TarInfo('pax')
        header.type, header.size = tarfile.XHDTYPE, backup.MAX_HEADER + 1
        stream = io.BytesIO(header.tobuf())
        with self.assertRaisesRegex(ValueError, 'metadata exceeds'):
            backup.restore(stream, *self.output())

    def test_truncated_payload(self):
        stream, _ = self.archive()
        with self.assertRaises((ValueError, tarfile.TarError)):
            backup.restore(io.BytesIO(stream.read()[:4500]), *self.output())

    def test_trailing_nonzero_data(self):
        stream, _ = self.archive()
        stream.seek(0, 2); stream.write(b'not padding'); stream.seek(0)
        with self.assertRaisesRegex(ValueError, 'after the backup'):
            backup.restore(stream, *self.output())


if __name__ == '__main__': unittest.main()
