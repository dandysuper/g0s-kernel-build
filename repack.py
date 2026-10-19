#!/usr/bin/env python3
"""
Repack the g0s kernel into a flashable zip.

Takes a freshly built kernel Image + module tree and produces:
  boot.img         - new kernel + the LineageOS 24.0 Android 17 init ramdisk
  vendor_boot.img  - Lineage first-stage ramdisk + dtb + rebuilt dlkm fragment
  vendor_dlkm.img  - EROFS modules partition built from the new modules

Mirrors BUILD.md sections 5-8, but substitutes the newly compiled kernel and
module set for the broken ExtremeKRNL ones.

Usage:
    python3 repack.py --image artifact/Image --modules modules_out/lib/modules
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(BASE, "work")

MKBOOTIMG = os.path.join(BASE, "mkbootimg.py")
MAKE_CPIO = os.path.join(BASE, "make_cpio.py")

VENDOR_DLKM_BLOCKS = 4806
VENDOR_DLKM_SIZE = VENDOR_DLKM_BLOCKS * 4096  # 19,685,376


def run(cmd, **kw):
    print("  $", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def run_quiet(cmd, **kw):
    """Run without echoing stdout. mkfs.erofs prints one line per file."""
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        subprocess.run(cmd, check=True, **kw)
    return proc


def kernel_vermagic(image_path):
    """Pull the vermagic string out of a raw arm64 Image."""
    out = subprocess.run(
        ["strings", image_path], capture_output=True, text=True, check=True
    ).stdout
    m = re.search(r"(5\.10\.\d+[^\n]*? SMP preempt[^\n]*)", out)
    return m.group(1).strip() if m else None


def module_vermagic(ko_path):
    out = subprocess.run(
        ["strings", ko_path], capture_output=True, text=True, check=True
    ).stdout
    m = re.search(r"vermagic=(5\.10\.\d+[^\n]*)", out)
    return m.group(1).strip() if m else None


def flatten_modules(modules_src, dest):
    """Copy every .ko into a flat lib/modules tree, like the stock images use."""
    os.makedirs(dest, exist_ok=True)
    found = 0
    for root, _, files in os.walk(modules_src):
        for f in files:
            if f.endswith(".ko"):
                src = os.path.join(root, f)
                dst = os.path.join(dest, f)
                if os.path.exists(dst):
                    print("  ! duplicate module basename:", f)
                shutil.copy2(src, dst)
                found += 1
    return found


def rewrite_dep(src_dep, dest_dep, prefix):
    """Rebuild modules.dep with every path rewritten to prefix + basename.

    The shipped images use absolute paths, and each partition has its own:
        vendor_ramdisk01  /lib/modules/foo.ko:             /lib/modules/bar.ko
        vendor_dlkm       /vendor_dlkm/lib/modules/foo.ko: /vendor_dlkm/lib/modules/bar.ko
    depmod on the runner emits build-tree relative paths (kernel/drivers/...),
    so each entry is reduced to its basename and re-prefixed for its partition.
    """
    entries = {}
    with open(src_dep, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or ":" not in line:
                continue
            target, deps = line.split(":", 1)
            name = os.path.basename(target.strip())
            entries[name] = [
                os.path.basename(d.strip()) for d in deps.split() if d.strip()
            ]

    with open(dest_dep, "w", encoding="utf-8") as fh:
        for name in sorted(entries):
            deps = "".join(f" {prefix}{d}" for d in entries[name])
            fh.write(f"{prefix}{name}:{deps}\n")
    return len(entries)


def build_ramdisk_fragment(module_dir, modules_load, out_cpio, out_lz4):
    """Build vendor_ramdisk01: modules + metadata, LZ4 legacy framed."""
    staging = os.path.join(WORK, "staging_vr1")
    shutil.rmtree(staging, ignore_errors=True)
    lib_mod = os.path.join(staging, "lib", "modules")
    os.makedirs(lib_mod, exist_ok=True)

    for f in os.listdir(module_dir):
        if f.endswith((".ko", ".dep", ".alias", ".softdep")):
            shutil.copy2(os.path.join(module_dir, f), os.path.join(lib_mod, f))

    # modules.load is the load ORDER, and it is the one file that must not be
    # regenerated: that ordering is what brings up the Wi-Fi/BT chain.
    shutil.copy2(modules_load, os.path.join(lib_mod, "modules.load"))

    run([sys.executable, MAKE_CPIO, staging, out_cpio])
    # -l is mandatory: Android's first-stage init wants legacy 0x184c2102 framing
    run(["lz4", "-l", "-12", "--favor-decSpeed", "-f", out_cpio, out_lz4])


def build_boot(image, out_boot):
    run([
        sys.executable, MKBOOTIMG,
        "--header_version", "4",
        "--kernel", image,
        "--ramdisk", os.path.join(WORK, "boot", "ramdisk"),
        "--os_version", "17.0.0",
        "--os_patch_level", "2026-10",
        "--output", out_boot,
    ])


def build_vendor_boot(fragment_lz4, out_vboot):
    run([
        sys.executable, MKBOOTIMG,
        "--header_version", "4",
        "--pagesize", "2048",
        "--base", "0x10000000",
        "--kernel_offset", "0x00008000",
        "--ramdisk_offset", "0x01000000",
        "--tags_offset", "0x00000100",
        "--dtb_offset", "0x01f00000",
        "--vendor_cmdline", " bootconfig",
        "--dtb", os.path.join(WORK, "vboot", "dtb"),
        "--vendor_bootconfig", os.path.join(WORK, "vboot", "bootconfig"),
        "--vendor_ramdisk", os.path.join(WORK, "vboot", "vendor_ramdisk00"),
        "--ramdisk_type", "dlkm",
        "--ramdisk_name", "dlkm",
        "--vendor_ramdisk_fragment", fragment_lz4,
        "--vendor_boot", out_vboot,
    ])


def build_vendor_dlkm(module_dir, modules_load, src_dep, out_img):
    staging = os.path.join(WORK, "staging_dlkm")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(os.path.join(staging, "etc"), exist_ok=True)
    lib_mod = os.path.join(staging, "lib", "modules")
    os.makedirs(lib_mod, exist_ok=True)

    etc_src = os.path.join(WORK, "dlkm", "etc")
    if os.path.isdir(etc_src):
        for f in os.listdir(etc_src):
            s = os.path.join(etc_src, f)
            if os.path.isfile(s):
                shutil.copy2(s, os.path.join(staging, "etc", f))

    for f in os.listdir(module_dir):
        if f.endswith((".ko", ".alias", ".softdep")):
            shutil.copy2(os.path.join(module_dir, f), os.path.join(lib_mod, f))

    shutil.copy2(modules_load, os.path.join(lib_mod, "modules.load"))

    # modules.dep is regenerated for this partition's mount point rather than
    # copied, so it can never carry a /lib/modules prefix that does not resolve.
    rewrite_dep(
        src_dep,
        os.path.join(lib_mod, "modules.dep"),
        "/vendor_dlkm/lib/modules/",
    )

    tmp_img = os.path.join(WORK, "vendor_dlkm_raw.img")

    # vendor_dlkm is a fixed 4806-block (19,685,376 byte) partition. A lz4hc,12
    # image of the stock module set is 19,079,168 bytes, so there is ~592 KB of
    # headroom - real, but not generous. Climb the compression ladder instead of
    # failing if a future module set grows past the ceiling.
    data = None
    for spec in ("lz4hc,12", "lz4hc,14", "lz4hc,16", "lz4,9"):
        run_quiet(["mkfs.erofs", "-b", "4096", "-z", spec, tmp_img, staging])
        size = os.path.getsize(tmp_img)
        print(f"  erofs {spec:<10} {size} bytes / {VENDOR_DLKM_SIZE} limit")
        if size <= VENDOR_DLKM_SIZE:
            with open(tmp_img, "rb") as fh:
                data = fh.read()
            print(f"  settled on: {spec}")
            break
    if data is None:
        sys.exit(
            "FATAL: vendor_dlkm does not fit even at maximum compression - "
            "the module set has outgrown the partition"
        )

    with open(out_img, "wb") as fh:
        fh.write(data + b"\x00" * (VENDOR_DLKM_SIZE - len(data)))
    print(f"  vendor_dlkm: {len(data)} bytes -> padded to {VENDOR_DLKM_SIZE}")


def package_zip(boot, vboot, dlkm, dtbo, out_zip):
    staging = os.path.join(WORK, "dist")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(os.path.join(staging, "files"))
    os.makedirs(os.path.join(staging, "META-INF", "com", "google", "android"))

    shutil.copy2(boot, os.path.join(staging, "files", "boot.img"))
    shutil.copy2(vboot, os.path.join(staging, "files", "vendor_boot.img"))
    shutil.copy2(dlkm, os.path.join(staging, "files", "vendor_dlkm.img"))
    shutil.copy2(dtbo, os.path.join(staging, "files", "dtbo.img"))

    src_meta = os.path.join(WORK, "ek", "META-INF", "com", "google", "android")
    shutil.copy2(
        os.path.join(src_meta, "update-binary"),
        os.path.join(staging, "META-INF", "com", "google", "android", "update-binary"),
    )

    script = """ui_print(" ");
ui_print("ExtremeKRNL rebuild - g0s / LineageOS 24.0");
ui_print("BCM4389 Wi-Fi + BCM BT + KernelSU Next");
ui_print(" ");

ui_print("Flashing boot image...");
package_extract_file("files/boot.img", "/dev/block/by-name/boot");

ui_print("Flashing vendor_boot image...");
package_extract_file("files/vendor_boot.img", "/dev/block/by-name/vendor_boot");

ui_print("Flashing dtbo image...");
package_extract_file("files/dtbo.img", "/dev/block/by-name/dtbo");

ui_print("Flashing vendor_dlkm partition...");
package_extract_file("files/vendor_dlkm.img", map_partition("vendor_dlkm"));
unmap_partition("vendor_dlkm");

ui_print(" ");
ui_print("Done. Reboot to system.");
"""
    with open(
        os.path.join(staging, "META-INF", "com", "google", "android", "updater-script"),
        "w",
        encoding="utf-8",
    ) as fh:
        fh.write(script)

    if os.path.exists(out_zip):
        os.remove(out_zip)

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _, files in os.walk(staging):
            for f in sorted(files):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, staging)
                if f == ".DS_Store" or "__MACOSX" in rel:
                    continue
                zf.write(full, rel)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="new kernel Image")
    ap.add_argument("--modules", required=True, help="lib/modules dir from modules_install")
    ap.add_argument("--out", default=os.path.join(BASE, "out"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    modules_load = os.path.join(WORK, "vr1", "lib", "modules", "modules.load")
    if not os.path.exists(modules_load):
        sys.exit(f"missing preserved modules.load at {modules_load}")

    print("== 1. flatten modules ==")
    flat = os.path.join(WORK, "flat_modules")
    shutil.rmtree(flat, ignore_errors=True)
    n = flatten_modules(args.modules, flat)
    print(f"  {n} modules")

    print("== 2. modules.dep ==")
    src_dep = None
    for root, _, files in os.walk(args.modules):
        if "modules.dep" in files:
            src_dep = os.path.join(root, "modules.dep")
            break
    if not src_dep:
        sys.exit("FATAL: no modules.dep found in the build output")
    n = rewrite_dep(src_dep, os.path.join(flat, "modules.dep"), "/lib/modules/")
    print(f"  {n} dep entries")
    for meta in ("modules.alias", "modules.softdep"):
        for root, _, files in os.walk(args.modules):
            if meta in files:
                shutil.copy2(os.path.join(root, meta), os.path.join(flat, meta))
                break

    print("== 3. vermagic check ==")
    kv = kernel_vermagic(args.image)
    sample = next(f for f in sorted(os.listdir(flat)) if f.endswith(".ko"))
    mv = module_vermagic(os.path.join(flat, sample))
    print(f"  kernel : {kv}")
    print(f"  module : {mv}")
    if not kv or not mv or kv != mv:
        sys.exit("FATAL: kernel/module vermagic mismatch - modules will not load")

    print("== 4. vendor_ramdisk01 ==")
    frag_cpio = os.path.join(WORK, "vr1_new.cpio")
    frag_lz4 = os.path.join(WORK, "vr1_new.lz4")
    build_ramdisk_fragment(flat, modules_load, frag_cpio, frag_lz4)

    print("== 5. boot.img ==")
    out_boot = os.path.join(args.out, "boot.img")
    build_boot(args.image, out_boot)

    print("== 6. vendor_boot.img ==")
    out_vboot = os.path.join(args.out, "vendor_boot.img")
    build_vendor_boot(frag_lz4, out_vboot)

    print("== 7. vendor_dlkm.img ==")
    out_dlkm = os.path.join(args.out, "vendor_dlkm.img")
    build_vendor_dlkm(flat, modules_load, src_dep, out_dlkm)

    print("== 8. package ==")
    out_zip = os.path.join(
        os.path.expanduser("~/Downloads"),
        "ExtremeKRNL-g0s-LineageOS24-rebuild-KSU.zip",
    )
    package_zip(
        out_boot, out_vboot, out_dlkm,
        os.path.join(WORK, "ek", "files", "dtbo.img"),
        out_zip,
    )

    print("\nDONE")
    for p in (out_boot, out_vboot, out_dlkm, out_zip):
        print(f"  {p}  {os.path.getsize(p)} bytes")


if __name__ == "__main__":
    main()
