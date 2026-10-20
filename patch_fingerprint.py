#!/usr/bin/env python3
"""
patch_fingerprint.py - Patch Samsung S22+ (Exynos 2200 / g0s) fingerprint.ko driver.

Problem:
  The stock fingerprint.ko driver returns -EINVAL (-22) when the HAL calls
  ENABLE_SPI_CLOCK or ENABLE_INTERRUPT if they are already enabled. This causes
  Samsung's libbauthserver / Qualcomm Trustlet to fail initialization.

Patch:
  Offset 0x2088 in fingerprint.ko:
    Original: 0x128002b4  (mov w20, #-0x16  -> -22 / -EINVAL)
    Patched:  0x2a1f03f4  (mov w20, wzr     -> 0 / Success)

Usage:
  python3 patch_fingerprint.py [input_file] [output_file]
  Default:
    input:  /vendor_dlkm/lib/modules/fingerprint.ko (or local fingerprint.ko)
    output: /data/adb/modules_fixed/fingerprint.ko
"""

import sys
import os

PATCH_OFFSET = 0x2088
ORIG_BYTES = b"\xb4\x02\x80\x12"  # mov w20, #-0x16
PATCH_BYTES = b"\xf4\x03\x1f\x2a"  # mov w20, wzr

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

    current_bytes = bytes(data[PATCH_OFFSET:PATCH_OFFSET+4])
    if current_bytes == PATCH_BYTES:
        print(f"File '{infile}' is already patched at offset {hex(PATCH_OFFSET)}.")
    elif current_bytes == ORIG_BYTES:
        data[PATCH_OFFSET:PATCH_OFFSET+4] = PATCH_BYTES
        os.makedirs(os.path.dirname(os.path.abspath(outfile)), exist_ok=True)
        with open(outfile, "wb") as f:
            f.write(data)
        print(f"Successfully patched '{infile}' -> '{outfile}' at offset {hex(PATCH_OFFSET)}.")
        print(f"Replaced {current_bytes.hex()} (mov w20, #-0x16) with {PATCH_BYTES.hex()} (mov w20, wzr).")
    else:
        # Search for pattern if offset differs across kernel revisions
        found = data.find(ORIG_BYTES)
        if found != -1:
            print(f"Warning: Pattern found at alternate offset {hex(found)} (expected {hex(PATCH_OFFSET)}).")
            data[found:found+4] = PATCH_BYTES
            os.makedirs(os.path.dirname(os.path.abspath(outfile)), exist_ok=True)
            with open(outfile, "wb") as f:
                f.write(data)
            print(f"Successfully patched '{infile}' -> '{outfile}' at offset {hex(found)}.")
        else:
            print(f"Error: Expected bytes {ORIG_BYTES.hex()} not found at {hex(PATCH_OFFSET)} (found {current_bytes.hex()}).")
            sys.exit(1)

if __name__ == "__main__":
    main()
