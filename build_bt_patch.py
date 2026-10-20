#!/usr/bin/env python3
"""Build a Bluetooth-only patch zip that flashes vendor_boot and nothing else.

The Bluetooth failure on ExtremeKRNL/LineageOS 24.0 is a module *load order*
problem: Android first-stage init loads /lib/modules/modules.load in strict
mode and aborts the entire list on the first module that fails. dhd.ko (Wi-Fi)
cannot initialise there because /vendor is not mounted yet, so it fails, and
every module after it is skipped -- including bcm43xx.ko, the Broadcom
Bluetooth power/rfkill driver.

The fix is therefore only the modules.load file inside vendor_ramdisk01, which
lives inside vendor_boot.img. Nothing else has to change: kernel, dtbo,
vendor_dlkm and the LineageOS fstab all stay exactly as they are on the device.

This script takes an already-extracted vendor_boot directory, rewrites only
modules.load, rebuilds vendor_boot.img with every other input byte-identical,
and packages a zip whose updater-script flashes only the vendor_boot partition.
"""

import os
import shutil
import subprocess
import sys
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "work", "btpatch")          # unpacked vendor_boot
VR1 = os.path.join(SRC, "vr1")                       # extracted vendor_ramdisk01
STAGE = os.path.join(BASE, "work", "btpatch_stage")  # rebuilt ramdisk
FILES = os.path.join(BASE, "work", "btpatch_zip")    # zip staging
OUT_IMG = os.path.join(BASE, "work", "btpatch_vendor_boot.img")
OUT_ZIP = os.path.expanduser("~/Downloads/bt-fix-g0s-vendor_boot-only.zip")

# Modules that must NOT appear in the early ramdisk list. dhd needs /vendor for
# firmware; the others need a mounted /vendor or a service that is not up yet.
BLOCKED = {
    "dhd",
    "fingerprint",
    "fingerprint_sysfs",
    "input_booster_lkm",
    "sec_debug_coredump",
}


def run(cmd, cwd=None):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit("failed: %s" % " ".join(cmd))
    return proc.stdout


def main():
    for path in (SRC, VR1):
        if not os.path.isdir(path):
            raise SystemExit("missing %s -- run the unpack step first" % path)

    load_path = os.path.join(VR1, "lib", "modules", "modules.load")
    if not os.path.isfile(load_path):
        raise SystemExit("missing %s" % load_path)

    with open(load_path) as handle:
        entries = [line.strip() for line in handle if line.strip()]

    kept = [name for name in entries if name not in BLOCKED]
    dropped = [name for name in entries if name in BLOCKED]

    print("modules.load entries : %d" % len(entries))
    print("kept                 : %d" % len(kept))
    print("dropped              : %s" % (", ".join(dropped) if dropped else "(none)"))

    if "bcm43xx" not in kept:
        raise SystemExit("bcm43xx missing from modules.load -- this is the whole fix")

    index = kept.index("bcm43xx")
    print("bcm43xx position     : %d of %d" % (index + 1, len(kept)))

    # Rebuild the ramdisk tree: everything from the source, modules.load edited.
    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    shutil.copytree(VR1, STAGE)

    with open(os.path.join(STAGE, "lib", "modules", "modules.load"), "w") as handle:
        for name in kept:
            handle.write(name + "\n")

    # Pack the cpio exactly the way repack.py does, then lz4 with the same flags.
    cpio_path = os.path.join(SRC, "vr1_patched.cpio")
    lz4_path = os.path.join(SRC, "vr1_patched.lz4")
    run([sys.executable, os.path.join(BASE, "make_cpio.py"), STAGE, cpio_path])
    run([
        "lz4", "-l", "-12", "--favor-decSpeed", "-f", cpio_path, lz4_path,
    ])
    print("vendor_ramdisk01     : %d bytes" % os.path.getsize(lz4_path))

    # Rebuild vendor_boot with the patched ramdisk01 fragment. Every other
    # input (dtb, bootconfig, vendor_ramdisk00) is reused from the source image,
    # so fstab, encryption and the device tree are untouched.
    run([
        sys.executable, os.path.join(BASE, "mkbootimg.py"),
        "--header_version", "4",
        "--pagesize", "2048",
        "--base", "0x10000000",
        "--kernel_offset", "0x00008000",
        "--ramdisk_offset", "0x01000000",
        "--tags_offset", "0x00000100",
        "--dtb_offset", "0x01f00000",
        "--dtb", os.path.join(SRC, "dtb"),
        "--vendor_bootconfig", os.path.join(SRC, "bootconfig"),
        "--vendor_ramdisk", os.path.join(SRC, "vendor_ramdisk00"),
        "--ramdisk_type", "dlkm",
        "--ramdisk_name", "dlkm",
        "--vendor_ramdisk_fragment", lz4_path,
        "--vendor_boot", OUT_IMG,
    ])
    print("vendor_boot.img      : %d bytes" % os.path.getsize(OUT_IMG))

    # Package: one image, one updater-script that touches only vendor_boot.
    if os.path.isdir(FILES):
        shutil.rmtree(FILES)
    meta = os.path.join(FILES, "META-INF", "com", "google", "android")
    os.makedirs(meta)
    os.makedirs(os.path.join(FILES, "files"))

    shutil.copy2(
        os.path.join(BASE, "work", "ek", "META-INF", "com", "google", "android",
                     "update-binary"),
        os.path.join(meta, "update-binary"),
    )
    shutil.copy2(OUT_IMG, os.path.join(FILES, "files", "vendor_boot.img"))

    script = """ui_print(" ");
ui_print("=========================================");
ui_print(" Bluetooth fix only - vendor_boot patch");
ui_print(" Device: Samsung Galaxy S22+ (g0s)");
ui_print(" Flashes ONE partition: vendor_boot");
ui_print("=========================================");
ui_print(" ");
ui_print("Kernel, dtbo and vendor_dlkm are NOT touched.");
ui_print(" ");
ui_print("Flashing vendor_boot image...");
package_extract_file("files/vendor_boot.img", "/dev/block/by-name/vendor_boot");
ui_print(" ");
ui_print("Done. Reboot and check Bluetooth.");
ui_print(" ");
"""
    with open(os.path.join(meta, "updater-script"), "w") as handle:
        handle.write(script)

    if os.path.exists(OUT_ZIP):
        os.remove(OUT_ZIP)
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _dirs, names in os.walk(FILES):
            for name in names:
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, FILES))

    print("zip                  : %s (%d bytes)" % (OUT_ZIP, os.path.getsize(OUT_ZIP)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
