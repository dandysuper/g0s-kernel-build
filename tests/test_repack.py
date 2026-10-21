import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

import repack


RELEASE = "5.10.260-test"
FLAGS = "SMP preempt mod_unload modversions aarch64"
MODULES = ("bcm43xx.ko", "cfg80211.ko", "dhd.ko", "fingerprint.ko", "fingerprint_sysfs.ko")


def write_module(path, release=RELEASE, flags=FLAGS):
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    header[16:20] = b"\x01\x00\xb7\x00"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + f"\x00vermagic={release} {flags}\x00".encode())


class RepackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kernel-artifact-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.image = self.root / "Image"
        header = bytearray(64)
        header[56:60] = b"ARM\x64"
        self.image.write_bytes(header + f"Linux version {RELEASE} (test builder)\x00".encode())
        self.modules = self.root / "modules"
        for name in MODULES:
            write_module(self.modules / "kernel/drivers" / name)
        self.dep = self.modules / "modules.dep"
        self.dep.write_text("".join(
            f"kernel/drivers/{name}:" + (" kernel/drivers/cfg80211.ko" if name == "dhd.ko" else "") + "\n"
            for name in MODULES
        ))
        self.early = self.root / "early.load"
        self.late = self.root / "late.load"
        self.early.write_text("bcm43xx\ncfg80211.ko\n")
        self.late.write_text("fingerprint_sysfs\nfingerprint.ko\ndhd\n")
        patch = mock.patch.object(repack, "WORK", str(self.work))
        patch.start()
        self.addCleanup(patch.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def verify(self):
        return repack.verify_modules(self.image, self.modules)

    def test_all_modules_are_verified_without_subprocesses(self):
        with mock.patch.object(repack.subprocess, "run", side_effect=AssertionError("unexpected process")):
            modules, metadata = self.verify()
        self.assertEqual(set(modules), set(MODULES))
        self.assertEqual(Path(metadata["modules.dep"]), self.dep)

    def test_release_token_must_match_exactly_not_just_prefix(self):
        self.image.write_bytes(self.image.read_bytes().replace(RELEASE.encode(), b"5.10.26"))
        with self.assertRaisesRegex(ValueError, "release mismatch"):
            self.verify()

    def test_a_non_sample_mismatched_module_is_rejected(self):
        write_module(self.modules / "kernel/drivers/fingerprint_sysfs.ko", "5.10.260-other")
        with self.assertRaisesRegex(ValueError, "fingerprint_sysfs"):
            self.verify()

    def test_mixed_module_abi_flags_are_rejected(self):
        write_module(self.modules / "kernel/drivers/fingerprint.ko", flags="SMP mod_unload aarch64")
        with self.assertRaisesRegex(ValueError, "mixed module ABI"):
            self.verify()

    def test_wrong_architecture_is_rejected(self):
        path = self.modules / "kernel/drivers/dhd.ko"
        data = bytearray(path.read_bytes())
        data[18:20] = b"\x3e\x00"
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "arm64 relocatable"):
            self.verify()

    def test_boot_container_is_not_accepted_as_raw_image(self):
        self.image.write_bytes(b"ANDROID!" + self.image.read_bytes())
        with self.assertRaisesRegex(ValueError, "not boot.img"):
            self.verify()

    def test_missing_required_driver_is_rejected(self):
        (self.modules / "kernel/drivers/fingerprint.ko").unlink()
        with self.assertRaisesRegex(ValueError, "required modules missing"):
            self.verify()

    def test_duplicate_module_basenames_fail_before_copying(self):
        write_module(self.modules / "another-release/dhd.ko")
        destination = self.root / "flat"
        with self.assertRaisesRegex(ValueError, "duplicate module"):
            repack.flatten_modules(self.modules, destination)
        self.assertFalse(destination.exists())

    def test_incomplete_or_unresolved_dependencies_are_rejected(self):
        original = self.dep.read_text()
        for text in (original.replace("cfg80211.ko\n", "missing.ko\n"), original.split("\n", 1)[1]):
            with self.subTest(text=text):
                self.dep.write_text(text)
                with self.assertRaisesRegex(ValueError, "dependencies|exactly"):
                    self.verify()

    def test_duplicate_dependency_targets_are_rejected(self):
        self.dep.write_text(self.dep.read_text() + "somewhere/dhd.ko:\n")
        with self.assertRaisesRegex(ValueError, "duplicate dependency target"):
            self.verify()

    def test_distinct_load_lists_are_validated_without_reordering(self):
        before = (self.early.read_bytes(), self.late.read_bytes())
        repack.validate_load_lists(self.early, self.late, MODULES)
        self.assertEqual((self.early.read_bytes(), self.late.read_bytes()), before)
        with self.early.open("a") as output:
            output.write("dhd\n")
        with self.assertRaisesRegex(ValueError, "strict init"):
            repack.validate_load_lists(self.early, self.late, MODULES)

    def test_missing_or_duplicate_load_entries_are_rejected(self):
        for text in ("dhd\nfingerprint\n", "dhd\nfingerprint\nfingerprint_sysfs\ndhd.ko\n"):
            self.late.write_text(text)
            with self.assertRaises(ValueError):
                repack.validate_load_lists(self.early, self.late, MODULES)

    def test_dependency_paths_are_rewritten_for_each_partition(self):
        output = self.root / "rewritten.dep"
        repack.rewrite_dep(self.dep, output, "/vendor_dlkm/lib/modules/")
        self.assertIn("/vendor_dlkm/lib/modules/dhd.ko: /vendor_dlkm/lib/modules/cfg80211.ko\n", output.read_text())
        self.assertNotIn("kernel/drivers", output.read_text())

    def test_verify_only_needs_no_rom_workspace_or_output_directory(self):
        output = self.root / "untouched"
        with mock.patch("sys.argv", ["repack.py", "--image", str(self.image), "--modules", str(self.modules),
                                     "--verify-only", "--out", str(output)]):
            repack.main()
        self.assertFalse(output.exists())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_main_keeps_early_and_late_manifests_separate(self):
        early = self.work / "vr1/lib/modules/modules.load"
        late = self.work / "dlkm/lib/modules/modules.load"
        for destination, source in ((early, self.early), (late, self.late)):
            destination.parent.mkdir(parents=True)
            destination.write_bytes(source.read_bytes())
        for relative in ("boot/ramdisk", "vboot/dtb", "vboot/bootconfig", "vboot/vendor_ramdisk00",
                         "ek/files/dtbo.img", "ek/META-INF/com/google/android/update-binary"):
            path = self.work / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"preserved")
        def create_output(*args):
            Path(args[-1]).write_bytes(b"new-image")
        stages = {name: mock.Mock(side_effect=create_output) for name in (
            "build_ramdisk_fragment", "build_boot", "build_vendor_boot", "build_vendor_dlkm", "package_zip")}
        output = self.root / "out"
        with mock.patch.multiple(repack, strip_modules=mock.Mock(), **stages), \
             mock.patch.object(repack.shutil, "which", return_value="/test/tool"), \
             mock.patch("sys.argv", ["repack.py", "--image", str(self.image), "--modules", str(self.modules),
                                     "--out", str(output)]):
            repack.main()
        self.assertEqual(stages["build_ramdisk_fragment"].call_args.args[1], str(early))
        self.assertEqual(stages["build_vendor_dlkm"].call_args.args[1], str(late))
        self.assertTrue((output / "ExtremeKRNL-g0s-LineageOS24-rebuild-KSU.zip").is_file())

    def test_debug_stripping_batches_multiple_inputs(self):
        directory = self.modules / "kernel/drivers"
        with mock.patch.object(repack.shutil, "which", return_value="/test/llvm-strip"), \
             mock.patch.object(repack.subprocess, "run") as process:
            repack.strip_modules(directory)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(process.call_args.args[0][:2], ["/test/llvm-strip", "--strip-debug"])
        self.assertEqual(len(process.call_args.args[0][2:]), len(MODULES))

    def test_objcopy_fallback_never_treats_another_module_as_output(self):
        def tool(name):
            return "/test/aarch64-linux-gnu-objcopy" if name.endswith("-objcopy") else None
        with mock.patch.object(repack.shutil, "which", side_effect=tool), \
             mock.patch.object(repack.subprocess, "run") as process:
            repack.strip_modules(self.modules / "kernel/drivers")
        self.assertEqual(process.call_count, len(MODULES))
        self.assertTrue(all(len(call.args[0]) == 3 for call in process.call_args_list))

    def erofs(self, size, output, returncode=0):
        def build(command, **kwargs):
            self.assertEqual(command[:5], ["mkfs.erofs", "-b", "4096", "-z", "lz4hc,12"])
            Path(command[-2]).write_bytes(b"E" * size)
            return subprocess.CompletedProcess(command, returncode, stdout="", stderr="fixture error" if returncode else "")
        with mock.patch.object(repack.subprocess, "run", side_effect=build) as process:
            repack.build_vendor_dlkm(self.modules / "kernel/drivers", self.late, self.dep, output)
        return process

    def test_erofs_uses_one_maximum_compression_pass_and_zero_padding(self):
        output = self.root / "vendor_dlkm.img"
        with mock.patch.object(repack, "VENDOR_DLKM_SIZE", 4096):
            process = self.erofs(128, output)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(output.read_bytes(), b"E" * 128 + b"\x00" * (4096 - 128))
        self.assertEqual((self.work / "staging_dlkm/lib/modules/modules.load").read_bytes(), self.late.read_bytes())
        self.assertEqual(repack.VENDOR_DLKM_SIZE, 19_685_376)

    def test_oversize_or_failed_erofs_retains_previous_image(self):
        output = self.root / "vendor_dlkm.img"
        for size, code, exception in ((4097, 0, ValueError), (0, 2, subprocess.CalledProcessError)):
            with self.subTest(code=code), mock.patch.object(repack, "VENDOR_DLKM_SIZE", 4096):
                output.write_bytes(b"known-good")
                with self.assertRaises(exception):
                    self.erofs(size, output, code)
                self.assertEqual(output.read_bytes(), b"known-good")
                self.assertEqual(list(self.root.glob(".vendor-dlkm-*")), [])

    @unittest.skipUnless(shutil.which("lz4") and shutil.which("cpio"), "LZ4/cpio unavailable")
    def test_ramdisk_fragment_uses_legacy_lz4_and_extractable_newc(self):
        archive = self.root / "fragment.cpio"
        compressed = self.root / "fragment.lz4"
        repack.build_ramdisk_fragment(self.modules / "kernel/drivers", self.early, str(archive), str(compressed))
        with compressed.open("rb") as source:
            self.assertEqual(source.read(4), b"\x02\x21\x4c\x18")
        decoded = subprocess.run(["lz4", "-dc", str(compressed)], check=True, capture_output=True, timeout=10).stdout
        self.assertEqual(decoded, archive.read_bytes())
        extracted = self.root / "ramdisk-extracted"
        extracted.mkdir()
        subprocess.run(["cpio", "-id", "--quiet", "--no-absolute-filenames"],
                       input=decoded, cwd=extracted, check=True, capture_output=True, timeout=10)
        self.assertEqual((extracted / "lib/modules/modules.load").read_bytes(), self.early.read_bytes())
        self.assertEqual((extracted / "lib/modules/dhd.ko").read_bytes(),
                         (self.modules / "kernel/drivers/dhd.ko").read_bytes())

    @unittest.skipUnless(shutil.which("mkfs.erofs") and shutil.which("fsck.erofs"), "EROFS tools unavailable")
    def test_real_erofs_fits_partition_and_preserves_late_module_metadata(self):
        output = self.root / "vendor_dlkm.img"
        repack.build_vendor_dlkm(self.modules / "kernel/drivers", self.late, self.dep, str(output))
        self.assertEqual(output.stat().st_size, repack.VENDOR_DLKM_SIZE)
        extracted = self.root / "erofs-extracted"
        subprocess.run(["fsck.erofs", "--extract=" + str(extracted), str(output)],
                       check=True, capture_output=True, timeout=15)
        modules = extracted / "lib/modules"
        self.assertEqual((modules / "modules.load").read_bytes(), self.late.read_bytes())
        self.assertEqual({path.name for path in modules.glob("*.ko")}, set(MODULES))
        self.assertIn("/vendor_dlkm/lib/modules/dhd.ko: /vendor_dlkm/lib/modules/cfg80211.ko",
                      (modules / "modules.dep").read_text())
        for name in MODULES:
            self.assertEqual((modules / name).read_bytes(), (self.modules / "kernel/drivers" / name).read_bytes())

    def package_inputs(self):
        binary = self.work / "ek/META-INF/com/google/android/update-binary"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"updater")
        paths = [self.root / name for name in ("boot.img", "vendor_boot.img", "vendor_dlkm.img", "dtbo.img")]
        for path in paths:
            path.write_bytes(path.name.encode())
        return paths

    def test_package_contains_all_four_matched_images(self):
        paths = self.package_inputs()
        output = self.root / "new/dist/kernel.zip"
        repack.package_zip(*paths, output)
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {
                "files/boot.img", "files/vendor_boot.img", "files/vendor_dlkm.img", "files/dtbo.img",
                "META-INF/com/google/android/update-binary", "META-INF/com/google/android/updater-script",
            })

    def test_package_failure_retains_previous_zip(self):
        paths = self.package_inputs()
        output = self.root / "kernel.zip"
        output.write_bytes(b"known-good-zip")
        with mock.patch.object(zipfile.ZipFile, "write", side_effect=OSError("disk fixture")):
            with self.assertRaises(OSError):
                repack.package_zip(*paths, output)
        self.assertEqual(output.read_bytes(), b"known-good-zip")
        self.assertEqual(list(self.root.glob(".kernel-package-*")), [])


if __name__ == "__main__":
    unittest.main()
