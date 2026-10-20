#!/usr/bin/env python3
"""Wrap a single vendor_boot.img into a flashable zip that touches only that partition.

Used to produce the Bluetooth patch in each kernel variant: the modules embedded
in vendor_ramdisk01 must match whatever kernel is on the device, so the patch
cannot be kernel-agnostic. Keeping the packager separate from the ramdisk
surgery makes that obvious and lets the same zip layout serve both builds.
"""

import argparse
import os
import shutil
import sys
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
UPDATE_BINARY = os.path.join(
    BASE, "work", "ek", "META-INF", "com", "google", "android", "update-binary"
)

SCRIPT_TEMPLATE = """ui_print(" ");
ui_print("=========================================");
ui_print(" Bluetooth fix only - vendor_boot patch");
ui_print(" Device: Samsung Galaxy S22+ (g0s)");
ui_print(" Flashes ONE partition: vendor_boot");
ui_print("=========================================");
ui_print(" ");
ui_print("Built for kernel: {kernel}");
ui_print("Kernel, dtbo and vendor_dlkm are NOT touched.");
ui_print(" ");
ui_print("Flashing vendor_boot image...");
package_extract_file("files/vendor_boot.img", "/dev/block/by-name/vendor_boot");
ui_print(" ");
ui_print("Done. Reboot and check Bluetooth.");
ui_print(" ");
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="vendor_boot.img to flash")
    parser.add_argument("--out", required=True, help="path of the zip to write")
    parser.add_argument("--kernel", required=True, help="kernel release for the banner")
    args = parser.parse_args()

    if not os.path.isfile(args.image):
        raise SystemExit("missing image: %s" % args.image)
    if not os.path.isfile(UPDATE_BINARY):
        raise SystemExit("missing update-binary: %s" % UPDATE_BINARY)

    staging = os.path.join(BASE, "work", "patchzip_stage")
    if os.path.isdir(staging):
        shutil.rmtree(staging)

    meta = os.path.join(staging, "META-INF", "com", "google", "android")
    os.makedirs(meta)
    os.makedirs(os.path.join(staging, "files"))

    shutil.copy2(UPDATE_BINARY, os.path.join(meta, "update-binary"))
    shutil.copy2(args.image, os.path.join(staging, "files", "vendor_boot.img"))

    with open(os.path.join(meta, "updater-script"), "w") as handle:
        handle.write(SCRIPT_TEMPLATE.format(kernel=args.kernel))

    out = os.path.expanduser(args.out)
    if os.path.exists(out):
        os.remove(out)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _dirs, names in os.walk(staging):
            for name in names:
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, staging))

    print("%s  (%d bytes)" % (out, os.path.getsize(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
