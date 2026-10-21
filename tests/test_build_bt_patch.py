import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import build_bt_patch
from make_cpio import write_cpio


class BluetoothPatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bluetooth-patch-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "work/btorig/u"
        self.ramdisk = self.source / "x"
        self.load = self.ramdisk / "lib/modules/modules.load"
        self.load.parent.mkdir(parents=True)
        self.raw = [f"driver{i}.ko" for i in range(305)]
        self.raw[10:10] = ["dhd.ko", "bcm43xx.ko"]
        self.raw[30:30] = ["fingerprint", "fingerprint_sysfs.ko", "sec_debug_coredump", "input_booster_lkm.ko"]
        self.load.write_text("\n".join(self.raw) + "\n")
        self.vendor = self.root / "work/dlkm/lib/modules/modules.load"
        self.vendor.parent.mkdir(parents=True)
        self.vendor.write_text("\n".join(sorted(build_bt_patch.DROP)) + "\n")
        for name in ("vendor_ramdisk01", "vendor_ramdisk00", "dtb", "bootconfig"):
            (self.source / name).write_bytes(name.encode())
        original = self.root / "work/btorig/files/vendor_boot.img"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"original")
        self.stage = self.root / "work/btmin_stage"
        self.output = self.root / "work/patched.img"
        for name, value in {"BASE": self.root, "SRC": self.source, "VR1": self.ramdisk,
                            "STAGE": self.stage, "OUT_IMG": self.output}.items():
            patch = mock.patch.object(build_bt_patch, name, str(value))
            patch.start()
            self.addCleanup(patch.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def invoke(self):
        def run(command):
            if str(command[1]).endswith("make_cpio.py"):
                write_cpio(command[-2], command[-1])
            else:
                Path(command[-1]).write_bytes(b"test-image")
            return ""
        with mock.patch.object(build_bt_patch, "run", side_effect=run) as commands:
            self.assertEqual(build_bt_patch.main(), 0)
        return commands

    def test_build_succeeds_and_defers_exactly_five_drivers(self):
        untouched = {name: (self.source / name).read_bytes()
                     for name in ("vendor_ramdisk00", "dtb", "bootconfig")}
        commands = self.invoke()
        result = (self.stage / "lib/modules/modules.load").read_text().splitlines()
        self.assertEqual(result, [name for name in self.raw if build_bt_patch.stem(name) not in build_bt_patch.DROP])
        self.assertIn("bcm43xx.ko", result)
        self.assertEqual(self.load.read_text().splitlines(), self.raw)
        self.assertEqual({name: (self.source / name).read_bytes() for name in untouched}, untouched)
        self.assertTrue(self.output.is_file())
        self.assertEqual(commands.call_count, 3)
        self.assertEqual(commands.call_args_list[1].args[0][:3], ["lz4", "-l", "-12"])

    def test_missing_vendor_list_fails_closed_before_repacking(self):
        self.vendor.unlink()
        with self.assertRaisesRegex(SystemExit, "cannot verify late driver"):
            self.invoke()
        self.assertFalse(self.output.exists())

    def test_vendor_manifest_drift_fails_closed(self):
        self.vendor.write_text(self.vendor.read_text() + "another_late_driver\n")
        with self.assertRaisesRegex(SystemExit, "unverified early/late"):
            self.invoke()
        self.assertFalse(self.stage.exists())

    def test_missing_bluetooth_module_rejects_wrong_source(self):
        self.load.write_text("\n".join(name for name in self.raw if name != "bcm43xx.ko") + "\n")
        with self.assertRaisesRegex(SystemExit, "wrong source tree"):
            self.invoke()

    def test_already_patched_source_is_not_patched_again(self):
        self.load.write_text("\n".join(name for name in self.raw if build_bt_patch.stem(name) not in build_bt_patch.DROP))
        with self.assertRaisesRegex(SystemExit, "already patched"):
            self.invoke()

    def test_mass_deletion_guard_is_preserved(self):
        self.load.write_text("\n".join(["bcm43xx"] + sorted(build_bt_patch.DROP)))
        with self.assertRaisesRegex(SystemExit, "expected at least"):
            self.invoke()


if __name__ == "__main__":
    unittest.main()
