#!/usr/bin/env python3
"""Build the Bluetooth-only patch: the stock vendor_boot with ONE line removed.

Measured against the real ExtremeKRNL release zip
(ExtremeKRNL-v2.5_g0s_AOSP_KSU_05-10-2026_03-11-33.zip):

  * modules.load has 319 entries.
  * `dhd` is entry 127.
  * `bcm43xx` is entry 180.
  * every other module that cannot load in first stage sits AFTER 180
    (sec_debug_coredump 258, input_booster_lkm 270, fingerprint 276,
     fingerprint_sysfs 277, s2dos05-regulator 282).

First-stage init loads that list in strict mode and aborts on the first
failure. `dhd` cannot initialise before /vendor is mounted (its firmware lives
there), so it fails at 127 and the remaining 192 entries are skipped --
including `bcm43xx`, the Broadcom Bluetooth power/rfkill driver. That is the
entire Bluetooth failure.

Therefore the fix is: keep the stock list, delete `dhd`, keep the order of
everything else. 318 entries, `bcm43xx` still at its original position, and
`s2dos05-regulator` and the fingerprint/input-booster modules deliberately left
in place even though they also cannot load early -- they are after bcm43xx, so
they cost nothing for Bluetooth, and removing them would change more of the
device than the patch needs to.

Only vendor_ramdisk01 inside vendor_boot.img changes. Kernel, dtbo,
vendor_dlkm, the LineageOS fstab and encryption are all reused untouched.
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

# The single entry to drop. Everything after it stays exactly where it was.
DROP = {"dhd"}

# Must survive the edit -- if this disappears the patch has no purpose.
REQUIRED = "bcm43xx"


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit("failed: %s" % " ".join(cmd))
    return proc.stdout


def main():
    for path in (SRC, VR1):
        if not os.path.isdir(path):
            raise SystemExit("missing %s -- unpack the stock vendor_boot first" % path)

    load_path = os.path.join(VR1, "lib", "modules", "modules.load")
    with open(load_path) as handle:
        raw = [line.rstrip("\n") for line in handle]
    raw = [line for line in raw if line.strip()]

    # The stock list writes names with a .ko suffix; compare on the stem so the
    # drop list is readable, then write the original spelling back unchanged.
    def stem(name):
        return name[:-3] if name.endswith(".ko") else name

    before = [stem(n) for n in raw]
    if REQUIRED not in before:
        raise SystemExit("%s missing from the stock list -- wrong source tree" % REQUIRED)

    kept = [n for n in raw if stem(n) not in DROP]
    dropped = [n for n in raw if stem(n) in DROP]

    print("stock entries        : %d" % len(raw))
    print("dropping             : %s" % (", ".join(dropped) if dropped else "(none)"))
    print("kept                 : %d" % len(kept))
    print("%-20s : stock %d, patched %d" % (
        REQUIRED,
        before.index(REQUIRED) + 1,
        [stem(n) for n in kept].index(REQUIRED) + 1,
    ))

    # Nothing that loads before bcm43xx may be removed, or the patch is wrong.
    if not dropped:
        raise SystemExit("nothing was dropped -- already patched?")
    bc = before.index(REQUIRED) + 1
    for name in dropped:
        pos = before.index(stem(name)) + 1
        if pos < bc:
            print("  note: %s was at %d, before %s at %d" % (name, pos, REQUIRED, bc))

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
        os.path.getsize(lz4_path), os.path.getsize(os.path.join(SRC, "vendor_ramdisk01"))))

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
