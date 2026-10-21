#!/usr/bin/env python3
"""Rebuild the dlkm ramdisk fragment so Wi-Fi, Bluetooth and fingerprint all load.

Ground truth is the real release zip
(ExtremeKRNL-v2.5_g0s_AOSP_KSU_05-10-2026_03-11-33.zip). Its vendor_boot carries
two ramdisks:

    vendor_ramdisk00   type 0x1         LineageOS init / fstab / encryption
    vendor_ramdisk01   type 0x3 "dlkm"  the module fragment we patch

The fragment's lib/modules/modules.load has 319 entries. Five of them cannot
succeed there, because their firmware or configuration only exists once /vendor
is mounted:

     127  dhd                  Broadcom Wi-Fi
     258  sec_debug_coredump
     270  input_booster_lkm
     276  fingerprint
     277  fingerprint_sysfs

Those five are not a random set. /vendor_dlkm ships
lib/modules/modules.load containing *exactly* those five, in that order:

    sec_debug_coredump
    fingerprint
    fingerprint_sysfs
    input_booster_lkm
    dhd

and all five .ko files are present in /vendor_dlkm. So the vendor's design is:
the fragment loads the bulk, and these five are loaded later from /vendor_dlkm,
at the point where /vendor exists.

The bug is that they are listed in the fragment as well. Module loading is
strict -- the first failure aborts the remainder -- so dhd failing at 127 skipped
`bcm43xx` at 180 (no Bluetooth) and everything after it, including the two
fingerprint modules. Worse, an aborted fragment list means init never gets as far
as processing /vendor_dlkm, so dhd never loads from there either: no Wi-Fi.

Removing exactly those five:

  * the fragment completes, so nothing after them is skipped;
  * `bcm43xx` (now at 179) loads, so Bluetooth works;
  * init proceeds to /vendor_dlkm, which loads dhd and the fingerprint modules
    with /vendor available, so Wi-Fi and fingerprint work.

Nothing else moves. `s2dos05-regulator` and every other entry keep their original
position and original spelling.

Only vendor_ramdisk01 inside vendor_boot.img is touched. The kernel, dtbo,
vendor_dlkm, LineageOS fstab and encryption are reused byte-identically, and the
zip writes to /dev/block/by-name/vendor_boot only.
"""

import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "work", "btorig", "u")     # unpacked stock vendor_boot
VR1 = os.path.join(SRC, "x")                        # extracted vendor_ramdisk01
STAGE = os.path.join(BASE, "work", "btmin_stage")
OUT_IMG = os.path.join(BASE, "work", "btmin_vendor_boot.img")

# The five modules /vendor_dlkm is responsible for. Removing them from the
# fragment list is what lets the fragment finish and /vendor_dlkm take over.
DROP = {
    "dhd",
    "fingerprint",
    "fingerprint_sysfs",
    "input_booster_lkm",
    "sec_debug_coredump",
}

# Must stay in the fragment: this is the module that makes Bluetooth work.
REQUIRED = ("bcm43xx",)

# Sanity floor -- if the list shrank far past this something is very wrong.
MIN_ENTRIES = 300


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit("failed: %s" % " ".join(cmd))
    return proc.stdout


def stem(name):
    """modules.load mixes `foo` and `foo.ko`; compare on the stem."""
    return name[:-3] if name.endswith(".ko") else name


def load_vendor_dlkm_list():
    """Require /vendor_dlkm's own load list before deferring its drivers."""
    path = os.path.join(BASE, "work", "dlkm", "lib", "modules", "modules.load")
    if not os.path.isfile(path):
        raise SystemExit("missing %s -- cannot verify late driver loading" % path)
    with open(path) as handle:
        return {stem(line.strip()) for line in handle if line.strip()}


def main():
    for path in (SRC, VR1):
        if not os.path.isdir(path):
            raise SystemExit("missing %s -- unpack the stock vendor_boot first" % path)

    load_path = os.path.join(VR1, "lib", "modules", "modules.load")
    if not os.path.isfile(load_path):
        raise SystemExit("missing %s" % load_path)

    with open(load_path) as handle:
        raw = [line.rstrip("\n") for line in handle if line.strip()]

    before = [stem(n) for n in raw]
    for name in REQUIRED:
        if name not in before:
            raise SystemExit("%s missing from the stock list -- wrong source tree" % name)

    # The drop set must be exactly what /vendor_dlkm promises to load. If it is
    # not, we would be deleting a module nothing else provides.
    vendor_list = load_vendor_dlkm_list()
    if vendor_list != DROP:
        raise SystemExit(
            "vendor_dlkm load list differs from the verified five-driver set; "
            "refusing an unverified early/late module split")
    print("/vendor_dlkm covers  : %s" % ", ".join(sorted(vendor_list)))

    dropped = [n for n in raw if stem(n) in DROP]
    if not dropped:
        raise SystemExit("nothing to drop -- is this already patched?")
    absent = DROP - {stem(n) for n in dropped}
    if absent:
        raise SystemExit("expected to drop but not in the list: %s"
                         % ", ".join(sorted(absent)))

    kept = [n for n in raw if stem(n) not in DROP]
    after = [stem(n) for n in kept]

    if len(kept) < MIN_ENTRIES:
        raise SystemExit("only %d entries left, expected at least %d"
                         % (len(kept), MIN_ENTRIES))

    print("stock entries        : %d" % len(raw))
    print("dropping             : %s" % ", ".join(dropped))
    print("patched entries      : %d" % len(kept))
    for name in REQUIRED:
        print("  %-12s %3d -> %3d" % (name, before.index(name) + 1, after.index(name) + 1))

    # Not one of the dropped modules may remain, and no dropped module's own
    # dependency may now be orphaned from the fragment's point of view -- the
    # fragment simply no longer mentions them at all.
    leftover = DROP & set(after)
    if leftover:
        raise SystemExit("still present after patch: %s" % ", ".join(sorted(leftover)))

    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    shutil.copytree(VR1, STAGE)
    with open(os.path.join(STAGE, "lib", "modules", "modules.load"), "w") as handle:
        for name in kept:
            handle.write(name + "\n")

    cpio_path = os.path.join(SRC, "vr1_patched.cpio")
    lz4_path = os.path.join(SRC, "vr1_patched.lz4")
    run([sys.executable, os.path.join(BASE, "make_cpio.py"), STAGE, cpio_path])
    run(["lz4", "-l", "-12", "--favor-decSpeed", "-f", cpio_path, lz4_path])
    print("vendor_ramdisk01     : %d bytes (stock %d)" % (
        os.path.getsize(lz4_path),
        os.path.getsize(os.path.join(SRC, "vendor_ramdisk01"))))

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
    print("vendor_boot.img      : %d bytes (stock %d)" % (
        os.path.getsize(OUT_IMG),
        os.path.getsize(os.path.join(BASE, "work", "btorig", "files", "vendor_boot.img"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
