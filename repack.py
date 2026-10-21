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
import mmap
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
        proc.check_returncode()
    return proc


def binary_string(path, pattern):
    """Inspect NUL-delimited metadata without subprocesses or whole-file copies."""
    with open(path, "rb") as source:
        if not os.fstat(source.fileno()).st_size:
            return None
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
            match = re.search(pattern, data)
            return match.group(1).decode("ascii").strip() if match else None


def kernel_release(image_path):
    release = binary_string(image_path, rb"Linux version (\d+\.\d+\.\d+[^\x00\s]*)[ \t]")
    if release:
        return release
    return binary_string(image_path, rb"(?:^|\x00)(\d+\.\d+\.\d+[^\x00\s]*) SMP ")


def module_vermagic(ko_path):
    return binary_string(ko_path, rb"(?:^|\x00)vermagic=([^\x00\r\n]+)")


def scan_module_tree(modules_src):
    modules = {}
    metadata = {}
    if not os.path.isdir(modules_src):
        raise ValueError(f"module directory does not exist: {modules_src}")
    for root, dirs, files in os.walk(modules_src):
        dirs.sort()
        for name in sorted(files):
            if name.endswith(".ko"):
                destination = modules
            elif name in ("modules.dep", "modules.alias", "modules.softdep"):
                destination = metadata
            else:
                continue
            if name in destination:
                raise ValueError(f"duplicate module/metadata basename: {name}; select one kernel release")
            destination[name] = os.path.join(root, name)
    if not modules:
        raise ValueError("no .ko modules found")
    return modules, metadata


def read_dependencies(src_dep):
    entries = {}
    with open(src_dep, encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            if ":" not in line:
                raise ValueError(f"malformed modules.dep line {number}")
            target, dependencies = line.split(":", 1)
            name = os.path.basename(target.strip())
            if not name.endswith(".ko") or name in entries:
                raise ValueError(f"invalid or duplicate dependency target: {name}")
            entries[name] = [os.path.basename(path) for path in dependencies.split()]
    return entries


def verify_modules(image_path, modules_src):
    modules, metadata = scan_module_tree(modules_src)
    with open(image_path, "rb") as image:
        if image.read(64)[56:60] != b"ARM\x64":
            raise ValueError("--image must be an uncompressed arm64 kernel Image, not boot.img")
    release = kernel_release(image_path)
    if not release:
        raise ValueError("no kernel release found in Image")
    required = {"dhd.ko", "cfg80211.ko", "bcm43xx.ko", "fingerprint.ko", "fingerprint_sysfs.ko"}
    missing = required - modules.keys()
    if missing:
        raise ValueError("required modules missing: " + ", ".join(sorted(missing)))
    expected = None
    for name, path in modules.items():
        with open(path, "rb") as module:
            header = module.read(20)
        if header[:6] != b"\x7fELF\x02\x01" or header[16:20] != b"\x01\x00\xb7\x00":
            raise ValueError(f"not an arm64 relocatable ELF module: {name}")
        vermagic = module_vermagic(path)
        # Match a complete release token, not a prefix (5.10.26 != 5.10.260).
        if not vermagic or vermagic.split()[0] != release or "aarch64" not in vermagic.split():
            raise ValueError(f"kernel/module release mismatch: {release} vs {vermagic!r} ({name})")
        if expected is not None and vermagic != expected:
            raise ValueError(f"mixed module ABI flags: {name}: {vermagic!r} != {expected!r}")
        expected = vermagic
    if "modules.dep" not in metadata:
        raise ValueError("no modules.dep found in the build output")
    entries = read_dependencies(metadata["modules.dep"])
    if entries.keys() != modules.keys():
        raise ValueError("modules.dep does not describe exactly the supplied module set")
    for name, dependencies in entries.items():
        unknown = set(dependencies) - modules.keys()
        if unknown:
            raise ValueError(f"unresolved dependencies for {name}: {', '.join(sorted(unknown))}")
    print(f"  kernel release: {release}")
    print(f"  module ABI: {expected}")
    print(f"  verified all {len(modules)} modules ({sum(os.path.getsize(p) for p in modules.values())} bytes)")
    return modules, metadata


def validate_load_lists(early_path, late_path, modules):
    def read_list(path):
        with open(path, encoding="utf-8") as source:
            return [line.split("#", 1)[0].strip() for line in source if line.split("#", 1)[0].strip()]

    def module_name(entry):
        return os.path.basename(entry).removesuffix(".ko").replace("-", "_")

    early = [module_name(entry) for entry in read_list(early_path)]
    late = [module_name(entry) for entry in read_list(late_path)]
    available = {module_name(name) for name in modules}
    if not early or not late:
        raise ValueError("both preserved early and vendor_dlkm modules.load lists are required")
    if len(early) != len(set(early)) or len(late) != len(set(late)):
        raise ValueError("duplicate entries in a preserved modules.load list")
    if (set(early) | set(late)) - available:
        raise ValueError("modules.load references modules absent from the build")
    if "bcm43xx" not in early:
        raise ValueError("Bluetooth bcm43xx must remain in the early load list")
    if not {"dhd", "fingerprint", "fingerprint_sysfs"} <= set(late):
        raise ValueError("vendor_dlkm must load Wi-Fi and fingerprint after /vendor mounts")
    if set(early) & set(late):
        raise ValueError("late-only modules remain in the early list; strict init may abort")


def strip_modules(module_dir):
    """Strip DWARF only, keeping module symbols and vermagic intact.

    KASAN instrumentation cannot be stripped; it must be disabled at build time.
    macOS /usr/bin/strip speaks Mach-O and is deliberately not used here.
    """
    tool = None
    for candidate in ("llvm-strip", "aarch64-linux-gnu-strip", "aarch64-linux-gnu-objcopy"):
        tool = shutil.which(candidate)
        if tool:
            break
    if tool is None:
        print("  ! no ELF-capable strip found; preserving modules unchanged")
        return

    paths = [os.path.join(module_dir, name) for name in sorted(os.listdir(module_dir)) if name.endswith(".ko")]
    before = sum(os.path.getsize(path) for path in paths)
    # strip supports many inputs; objcopy treats a second path as an output.
    batch_size = 1 if tool.endswith("-objcopy") else 128
    for start in range(0, len(paths), batch_size):
        subprocess.run([tool, "--strip-debug", *paths[start:start + batch_size]], check=True)
    after = sum(os.path.getsize(path) for path in paths)
    print(f"  {tool}: {before} -> {after} bytes (freed {before - after})")


def flatten_modules(modules_src, dest):
    """Copy one unambiguous kernel release into the stock flat module layout."""
    modules, _ = scan_module_tree(modules_src)
    os.makedirs(dest, exist_ok=True)
    for name, source in modules.items():
        shutil.copy2(source, os.path.join(dest, name))
    return len(modules)


def rewrite_dep(src_dep, dest_dep, prefix):
    """Rebuild modules.dep with every path rewritten to prefix + basename.

    The shipped images use absolute paths, and each partition has its own:
        vendor_ramdisk01  /lib/modules/foo.ko:             /lib/modules/bar.ko
        vendor_dlkm       /vendor_dlkm/lib/modules/foo.ko: /vendor_dlkm/lib/modules/bar.ko
    depmod on the runner emits build-tree relative paths (kernel/drivers/...),
    so each entry is reduced to its basename and re-prefixed for its partition.
    """
    entries = read_dependencies(src_dep)
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

    # Maximum supported LZ4HC compression first and only. Lower/unsupported
    # levels already failed to solve the measured partition-size problem.
    # Build beside the destination so failure cannot replace a valid image.
    directory = os.path.dirname(os.path.abspath(out_img))
    with tempfile.NamedTemporaryFile(prefix=".vendor-dlkm-", dir=directory, delete=False) as output:
        temporary = output.name
    try:
        run_quiet(["mkfs.erofs", "-b", "4096", "-z", "lz4hc,12", temporary, staging])
        size = os.path.getsize(temporary)
        if not 0 < size <= VENDOR_DLKM_SIZE:
            raise ValueError(f"vendor_dlkm is {size} bytes; fixed partition limit is {VENDOR_DLKM_SIZE}")
        # truncate extends with zeros without allocating an image-sized Python
        # buffer. Rename only after the complete padded file has been flushed.
        with open(temporary, "r+b") as output:
            output.truncate(VENDOR_DLKM_SIZE)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, out_img)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"  vendor_dlkm: {size} bytes -> padded to {VENDOR_DLKM_SIZE}")


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

    directory = os.path.dirname(os.path.abspath(out_zip))
    os.makedirs(directory, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".kernel-package-", dir=directory, delete=False) as output:
        temporary = output.name
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for root, dirs, files in os.walk(staging):
                dirs.sort()
                for f in sorted(files):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, staging).replace(os.sep, "/")
                    if f == ".DS_Store" or "__MACOSX" in rel:
                        continue
                    zf.write(full, rel)
        os.replace(temporary, out_zip)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="new kernel Image")
    ap.add_argument("--modules", required=True, help="lib/modules dir from modules_install")
    ap.add_argument("--out", default=os.path.join(BASE, "out"))
    ap.add_argument("--zip", help="output package path (defaults to --out/ExtremeKRNL-g0s-LineageOS24-rebuild-KSU.zip)")
    ap.add_argument("--verify-only", action="store_true", help="verify Image and every module without repacking")
    args = ap.parse_args()

    print("== verify Image, every module and dependency ==")
    try:
        modules, metadata = verify_modules(args.image, args.modules)
        if args.verify_only:
            return
        early_load = os.path.join(WORK, "vr1", "lib", "modules", "modules.load")
        late_load = os.path.join(WORK, "dlkm", "lib", "modules", "modules.load")
        validate_load_lists(early_load, late_load, modules)
        for relative in ("boot/ramdisk", "vboot/dtb", "vboot/bootconfig", "vboot/vendor_ramdisk00",
                         "ek/files/dtbo.img", "ek/META-INF/com/google/android/update-binary"):
            if not os.path.isfile(os.path.join(WORK, relative)):
                raise ValueError(f"missing preserved input: work/{relative}")
        for tool in ("lz4", "mkfs.erofs"):
            if not shutil.which(tool):
                raise ValueError(f"required host tool unavailable: {tool}")
    except (OSError, ValueError) as error:
        ap.exit(1, f"FATAL: {error}\n")

    os.makedirs(args.out, exist_ok=True)
    print("== 1. flatten modules ==")
    flat = os.path.join(WORK, "flat_modules")
    shutil.rmtree(flat, ignore_errors=True)
    n = flatten_modules(args.modules, flat)
    print(f"  {n} modules")

    print("== 1b. strip debug info ==")
    strip_modules(flat)
    total = sum(
        os.path.getsize(os.path.join(flat, f))
        for f in os.listdir(flat)
        if f.endswith(".ko")
    )
    print(f"  {total} bytes of modules vs {VENDOR_DLKM_SIZE} partition")

    print("== 2. modules.dep ==")
    src_dep = metadata["modules.dep"]
    n = rewrite_dep(src_dep, os.path.join(flat, "modules.dep"), "/lib/modules/")
    print(f"  {n} dep entries")
    for name in ("modules.alias", "modules.softdep"):
        if name in metadata:
            shutil.copy2(metadata[name], os.path.join(flat, name))

    print("== 3. verify stripped modules ==")
    verify_modules(args.image, flat)

    print("== 4. vendor_ramdisk01 ==")
    frag_cpio = os.path.join(WORK, "vr1_new.cpio")
    frag_lz4 = os.path.join(WORK, "vr1_new.lz4")
    build_ramdisk_fragment(flat, early_load, frag_cpio, frag_lz4)

    print("== 5. boot.img ==")
    out_boot = os.path.join(args.out, "boot.img")
    build_boot(args.image, out_boot)

    print("== 6. vendor_boot.img ==")
    out_vboot = os.path.join(args.out, "vendor_boot.img")
    build_vendor_boot(frag_lz4, out_vboot)

    print("== 7. vendor_dlkm.img ==")
    out_dlkm = os.path.join(args.out, "vendor_dlkm.img")
    build_vendor_dlkm(flat, late_load, src_dep, out_dlkm)

    print("== 8. package ==")
    out_zip = os.path.abspath(os.path.expanduser(args.zip)) if args.zip else os.path.join(
        args.out, "ExtremeKRNL-g0s-LineageOS24-rebuild-KSU.zip",
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
