"""Host tests for the legacy binary fingerprint.ko patcher."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "patch_fingerprint.py"
OFFSET = 0x2088
ORIGINAL = bytes.fromhex("b4028012")
PATCHED = bytes.fromhex("f4031f2a")


class PatchFingerprintTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="patch-fingerprint-test-")
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def module(self, at_offset, elsewhere=None):
        data = bytearray(range(256)) * ((OFFSET + 0x100) // 256 + 1)
        data[OFFSET:OFFSET + 4] = at_offset
        if elsewhere:
            data[0x40:0x44] = elsewhere
        path = self.dir / "fingerprint.ko"
        path.write_bytes(data)
        return path, bytes(data)

    def patch(self, source, output):
        return subprocess.run([sys.executable, str(SCRIPT), str(source), str(output)],
                              capture_output=True, text=True, timeout=30)

    def test_patches_only_the_known_instruction(self):
        source, original = self.module(ORIGINAL)
        output = self.dir / "out" / "fingerprint.ko"
        result = self.patch(source, output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        patched = output.read_bytes()
        self.assertEqual(patched[OFFSET:OFFSET + 4], PATCHED)
        self.assertEqual(patched[:OFFSET] + patched[OFFSET + 4:], original[:OFFSET] + original[OFFSET + 4:])
        self.assertEqual(source.read_bytes(), original)

    def test_already_patched_module_is_accepted_unchanged(self):
        source, original = self.module(PATCHED)
        result = self.patch(source, self.dir / "out.ko")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already patched", result.stdout)
        self.assertEqual(source.read_bytes(), original)

    def test_other_layouts_are_refused_even_if_the_bytes_exist_elsewhere(self):
        # A module compiled with the source fix must never have an unrelated
        # -EINVAL path rewritten by a pattern search.
        source, original = self.module(b"\x00\x00\x00\x00", elsewhere=ORIGINAL)
        output = self.dir / "out.ko"
        result = self.patch(source, output)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(output.exists())
        self.assertIn("patches/0001-fingerprint-qbt2000-idempotent-irq-toggles.patch", result.stdout)
        self.assertEqual(source.read_bytes(), original)

    def test_truncated_module_is_refused(self):
        source = self.dir / "short.ko"
        source.write_bytes(b"\x7fELF")
        output = self.dir / "out.ko"
        result = self.patch(source, output)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
