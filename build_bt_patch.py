#!/usr/bin/env python3
"""Build the vendor_boot patch: Bluetooth + fingerprint, one partition, no kernel change.

Ground truth is the real release zip
(ExtremeKRNL-v2.5_g0s_AOSP_KSU_05-10-2026_03-11-33.zip), whose vendor_ramdisk01
carries a 319-entry modules.load:

    127  dhd                 <- fails here, /vendor not mounted yet
    180  bcm43xx             <- Broadcom BT power/rfkill: NEVER REACHED
    258  sec_debug_coredump
    270  input_booster_lkm
    276  fingerprint
    277  fingerprint_sysfs   <- NEVER REACHED
    282  s2dos05-regulator

Android first-stage init loads this list in **strict mode** and aborts the whole
thing on the first module that fails. `dhd` cannot probe before /vendor is
mounted because its firmware lives there, so it dies at 127 and entries 128-319
are skipped. That single abort is why Bluetooth is dead AND why fingerprint
enrolment never completes: `bcm43xx` and the two fingerprint modules are all
downstream of it.

The patch therefore does two things and nothing else:

  1. delete `dhd` from the list, so loading no longer aborts at 127;
  2. move `sec_debug_coredump` and `input_booster_lkm` to the tail.

Step 2 matters because those two sit between `bcm43xx` (180) and `fingerprint`
(276). If either one aborts once dhd is gone, fingerprint is skipped all over
again. Moving them to the end costs nothing -- they are debug/booster modules
that get their dependencies later anyway -- and it guarantees both payloads are
reached before anything risky runs.

Everything else keeps its exact original position, `s2dos05-regulator` included
(it is a PMIC regulator and there is no reason to touch it).

Only vendor_ramdisk01 inside vendor_boot.img changes. The kernel, dtbo,
vendor_dlkm, the LineageOS fstab and encryption are reused byte-identically.
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

# Confirmed failure: dhd cannot probe before /vendor is mounted.
DROP = {"dhd"}

# Sits between bcm43xx and fingerprint. Delayed rather than deleted so they
# cannot abort the list before the modules we actually need are reached.
DEFER_TO_END = {"sec_debug_coredump", "input_booster_lkm"}

# If any of these disappear the patch has no purpose.
REQUIRED = ("bcm43xx", "fingerprint", "fingerprint_sysfs")


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

    dropped = [n for n in raw if stem(n) in DROP]
    if not dropped:
        raise SystemExit("nothing to drop -- is this already patched?")

    body = [n for n in raw if stem(n) not in DROP]
    deferred = [n for n in body if stem(n) in DEFER_TO_END]
    body = [n for n in body if stem(n) not in DEFER_TO_END]
    kept = body + deferred

    after = [stem(n) for n in kept]

    print("stock entries        : %d" % len(raw))
    print("dropping             : %s" % ", ".join(dropped))
    print("deferred to tail     : %s" % (", ".join(deferred) if deferred else "(none)"))
    print("patched entries      : %d" % len(kept))

    for name in REQUIRED:
        src = before.index(name) + 1
        dst = after.index(name) + 1
        print("  %-18s %3d -> %3d" % (name, src, dst))
        if dst > len(body):
            raise SystemExit("%s ended up inside the deferred tail" % name)

    # Every module we need must now sit before the deferred block, or a failure
    # in that block could still skip it.
    last_required = max(after.index(n) for n in REQUIRED)
    if deferred and last_required >= len(body):
        raise SystemExit("required modules are not ahead of the deferred tail")

    print("  highest required index: %d of %d (tail starts at %d)"
          % (last_required + 1, len(kept), len(body) + 1))

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
