#!/usr/bin/env python3
"""
patch_fingerprint.py - Binary fingerprint.ko fix for older g0s kernel builds.

Kernels built by .github/workflows/build.yml compile this fix from source
(patches/0001-fingerprint-qbt2000-idempotent-irq-toggles.patch), so their
fingerprint.ko must be used unmodified. This tool exists only for the module
of the earlier 5.10.260-g0d060df61c11-dirty build, which lacks the source fix.

Problem:
  qbt2000_enable_ipc(), qbt2000_disable_ipc(), qbt2000_enable_wuhb() and
  qbt2000_disable_wuhb() return -EINVAL (-22) when the interrupt is already in
  the requested state. Samsung's biometrics HAL treats that as fatal and aborts
  enrollment halfway.

Patch:
  File offset 0x2088 (.text+0x1dd4 in qbt2000_ioctl) is the shared
  rc = -EINVAL tail of exactly those four paths:
    Original: b4 02 80 12  (mov w20, #-0x16  -> -22 / -EINVAL)
    Patched:  f4 03 1f 2a  (mov w20, wzr     -> 0 / Success)
  Any other layout is refused: identical bytes elsewhere belong to unrelated
  error paths, so searching for them would corrupt a different driver path.

Usage:
  python3 patch_fingerprint.py [input_file] [output_file]
  Defaults: fingerprint.ko -> fingerprint_fixed.ko
"""

import os
import sys

PATCH_OFFSET = 0x2088
ORIG_BYTES = b"\xb4\x02\x80\x12"  # mov w20, #-0x16
PATCH_BYTES = b"\xf4\x03\x1f\x2a"  # mov w20, wzr
SOURCE_PATCH = "patches/0001-fingerprint-qbt2000-idempotent-irq-toggles.patch"


def main():
    infile = sys.argv[1] if len(sys.argv) > 1 else "fingerprint.ko"
    outfile = sys.argv[2] if len(sys.argv) > 2 else "fingerprint_fixed.ko"

    if not os.path.isfile(infile):
        print(f"Error: Input file '{infile}' not found.")
        print("Provide path to input fingerprint.ko: python3 patch_fingerprint.py <input> [output]")
        sys.exit(1)

    with open(infile, "rb") as f:
        data = bytearray(f.read())

    if len(data) < PATCH_OFFSET + 4:
        print(f"Error: File '{infile}' is too small ({len(data)} bytes).")
        sys.exit(1)

    current_bytes = bytes(data[PATCH_OFFSET:PATCH_OFFSET + 4])
    if current_bytes == PATCH_BYTES:
        print(f"File '{infile}' is already patched at offset {hex(PATCH_OFFSET)}.")
        return
    if current_bytes != ORIG_BYTES:
        print(f"Error: Expected bytes {ORIG_BYTES.hex()} at {hex(PATCH_OFFSET)}, found {current_bytes.hex()}.")
        print("This is not the known 5.10.260-g0d060df61c11-dirty module; nothing was written.")
        print(f"Kernels built with {SOURCE_PATCH} already include the fix: use their fingerprint.ko as is.")
        sys.exit(1)

    data[PATCH_OFFSET:PATCH_OFFSET + 4] = PATCH_BYTES
    os.makedirs(os.path.dirname(os.path.abspath(outfile)), exist_ok=True)
    with open(outfile, "wb") as f:
        f.write(data)
    print(f"Successfully patched '{infile}' -> '{outfile}' at offset {hex(PATCH_OFFSET)}.")
    print(f"Replaced {current_bytes.hex()} (mov w20, #-0x16) with {PATCH_BYTES.hex()} (mov w20, wzr).")


if __name__ == "__main__":
    main()
