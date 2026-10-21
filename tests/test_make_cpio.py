import io
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from make_cpio import COPY_BUFFER_SIZE, write_cpio, write_header


def parse_newc(data):
    result = {}
    offset = 0
    while True:
        if offset % 4 or data[offset:offset + 6] != b"070701":
            raise AssertionError(f"misaligned or invalid header at {offset}")
        fields = [int(data[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        name_start = offset + 110
        name_end = name_start + fields[11]
        if data[name_end - 1] != 0:
            raise AssertionError("missing filename terminator")
        name = os.fsdecode(data[name_start:name_end - 1])
        start = (name_end + 3) & ~3
        end = start + fields[6]
        offset = (end + 3) & ~3
        if name == "TRAILER!!!":
            if any(data[offset:]) or len(data) % 512:
                raise AssertionError("invalid archive trailer padding")
            return result
        result[name] = (fields, data[start:end])


class CpioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="android-cpio-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "ramdisk.cpio"

    def archive(self):
        write_cpio(self.source, self.output)
        return parse_newc(self.output.read_bytes())

    def test_headers_alignment_and_binary_payloads(self):
        for length in range(1, 13):
            (self.source / ("x" * length)).write_bytes(bytes(range(length)))
        entries = self.archive()
        self.assertEqual(len(entries), 12)
        for length in range(1, 13):
            fields, payload = entries["x" * length]
            self.assertEqual(payload, bytes(range(length)))
            self.assertEqual((fields[2], fields[3], fields[5]), (0, 0, 0))
            self.assertEqual(fields[1], 0o100644)

    def test_empty_tree_has_valid_trailer(self):
        self.assertEqual(self.archive(), {})
        self.assertEqual(self.output.stat().st_size, 512)

    def test_executables_and_directories_keep_bootable_modes(self):
        directory = self.source / "lib/modules"
        directory.mkdir(parents=True)
        script = self.source / "init"
        script.write_bytes(b"#!/system/bin/sh\n")
        script.chmod(0o700)
        (directory / "dhd.ko").write_bytes(b"module")
        entries = self.archive()
        self.assertEqual(entries["init"][0][1], 0o100755)
        self.assertEqual(entries["lib"][0][1], 0o40755)
        self.assertEqual(entries["lib/modules"][0][1], 0o40755)
        self.assertLess(list(entries).index("lib"), list(entries).index("lib/modules"))

    def test_file_directory_and_dangling_symlinks_are_not_dereferenced(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret").write_text("must not be archived")
        (self.source / "file-link").symlink_to(outside / "secret")
        (self.source / "dir-link").symlink_to(outside, target_is_directory=True)
        (self.source / "dangling").symlink_to("missing")
        entries = self.archive()
        self.assertEqual(set(entries), {"file-link", "dir-link", "dangling"})
        for fields, _ in entries.values():
            self.assertTrue(stat.S_ISLNK(fields[1]))
        self.assertEqual(entries["dangling"][1], b"missing")
        self.assertEqual(entries["dir-link"][1], os.fsencode(outside))

    def test_archive_is_reproducible_across_host_mtime_changes(self):
        module = self.source / "dhd.ko"
        module.write_bytes(b"driver")
        self.archive()
        first = self.output.read_bytes()
        os.utime(module, (1000000000, 1000000000))
        self.archive()
        self.assertEqual(self.output.read_bytes(), first)

    def test_regular_files_are_copied_with_a_bounded_buffer(self):
        (self.source / "large.ko").write_bytes(b"m" * (COPY_BUFFER_SIZE + 17))
        with mock.patch("make_cpio.shutil.copyfileobj", wraps=shutil.copyfileobj) as copy:
            entries = self.archive()
        self.assertEqual(copy.call_args.args[2], COPY_BUFFER_SIZE)
        self.assertEqual(len(entries["large.ko"][1]), COPY_BUFFER_SIZE + 17)

    def test_output_inside_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside the source"):
            write_cpio(self.source, self.source / "recursive.cpio")
        self.assertEqual(list(self.source.iterdir()), [])

    def test_missing_source_does_not_replace_existing_archive(self):
        self.output.write_bytes(b"known-good")
        with self.assertRaises(NotADirectoryError):
            write_cpio(self.root / "missing", self.output)
        self.assertEqual(self.output.read_bytes(), b"known-good")

    def test_fifo_is_rejected_without_hanging_or_replacing_archive(self):
        os.mkfifo(self.source / "pipe")
        self.output.write_bytes(b"known-good")
        with self.assertRaisesRegex(ValueError, "unsupported ramdisk entry"):
            self.archive()
        self.assertEqual(self.output.read_bytes(), b"known-good")
        self.assertEqual(list(self.root.glob(".ramdisk-*")), [])

    def test_newc_field_overflow_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exceeds 32 bits"):
            write_header(io.BytesIO(), "oversize", 1, 0o100644, 1 << 32)


if __name__ == "__main__":
    unittest.main()
