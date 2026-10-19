# Build Guide: LineageOS 24.0 + ExtremeKRNL Repack for Samsung Galaxy S22+ (`g0s`)

This document outlines the exact toolchain, procedures, and commands used to unpack, fix, rebuild, and package the custom kernel and ROM images.

---

## 1. Required Host Tools & Setup

All operations were executed on macOS (`arm64`). Install the required command-line utilities using Homebrew:

```bash
# 1. Install file-system and compression utilities
brew install lz4 erofs-utils brotli dtc

# 2. Verify installed binaries
which lz4         # /opt/homebrew/bin/lz4
which mkfs.erofs  # /opt/homebrew/bin/mkfs.erofs
which fsck.erofs  # /opt/homebrew/bin/fsck.erofs
which brotli      # /opt/homebrew/bin/brotli
which dtc         # /opt/homebrew/bin/dtc
which python3     # Python 3.10+
```

---

## 2. AOSP Boot Image Tools

Download the official Android Open Source Project (AOSP) boot image unpack and packing scripts:

```bash
# Fetch mkbootimg and unpack_bootimg directly from AOSP main branch
curl -sL "https://android.googlesource.com/platform/system/tools/mkbootimg/+/refs/heads/main/unpack_bootimg.py?format=TEXT" | base64 -d > unpack_bootimg.py
curl -sL "https://android.googlesource.com/platform/system/tools/mkbootimg/+/refs/heads/main/mkbootimg.py?format=TEXT" | base64 -d > mkbootimg.py
chmod +x unpack_bootimg.py mkbootimg.py
```

> [!NOTE]
> In `mkbootimg.py`, the GKI certification import is made optional so it runs without external Google-internal dependencies:
> ```python
> try:
>     from gki.generate_gki_certificate import generate_gki_certificate
> except ImportError:
>     generate_gki_certificate = None
> ```

---

## 3. CPIO Ramdisk Generator (`make_cpio.py`)

Standard macOS `bsdcpio` does not reliably guarantee strict POSIX `newc` 4-byte header and payload alignment with normalized `0:0` ownership. We use a deterministic Python script ([`make_cpio.py`](file:///Users/danb/Documents/kernel/make_cpio.py)):

```python
# make_cpio.py
import os, sys

def write_cpio(src_dir, out_file):
    with open(out_file, "wb") as out:
        ino = 1
        for root, dirs, files in os.walk(src_dir):
            dirs.sort()
            files.sort()
            rel_root = os.path.relpath(root, src_dir)
            if rel_root == ".":
                rel_root = ""

            # Write directory entry
            if rel_root:
                name_bytes = rel_root.encode("utf-8") + b"\x00"
                namesize = len(name_bytes)
                mode = 0o40755
                filesize = 0
                hdr = f"070701{ino:08x}{mode:08x}{0:08x}{0:08x}{2:08x}{0:08x}{filesize:08x}{0:08x}{0:08x}{0:08x}{0:08x}{namesize:08x}{0:08x}".encode("ascii")
                out.write(hdr)
                out.write(name_bytes)
                pad1 = (4 - ((110 + namesize) % 4)) % 4
                out.write(b"\x00" * pad1)
                ino += 1

            # Write file entries
            for f in files:
                full_path = os.path.join(root, f)
                rel_path = os.path.join(rel_root, f) if rel_root else f
                name_bytes = rel_path.encode("utf-8") + b"\x00"
                namesize = len(name_bytes)
                st = os.stat(full_path)
                mode = 0o100644
                filesize = st.st_size
                hdr = f"070701{ino:08x}{mode:08x}{0:08x}{0:08x}{1:08x}{0:08x}{filesize:08x}{0:08x}{0:08x}{0:08x}{0:08x}{namesize:08x}{0:08x}".encode("ascii")
                out.write(hdr)
                out.write(name_bytes)
                pad1 = (4 - ((110 + namesize) % 4)) % 4
                out.write(b"\x00" * pad1)
                with open(full_path, "rb") as content_file:
                    out.write(content_file.read())
                pad2 = (4 - (filesize % 4)) % 4
                out.write(b"\x00" * pad2)
                ino += 1

        # Trailer
        trailer_name = b"TRAILER!!!\x00"
        namesize = len(trailer_name)
        hdr = f"070701{0:08x}{0:08x}{0:08x}{0:08x}{1:08x}{0:08x}{0:08x}{0:08x}{0:08x}{0:08x}{0:08x}{namesize:08x}{0:08x}".encode("ascii")
        out.write(hdr)
        out.write(trailer_name)
        pad = (4 - ((110 + namesize) % 4)) % 4
        out.write(b"\x00" * pad)
        curr_pos = out.tell()
        pad512 = (512 - (curr_pos % 512)) % 512
        out.write(b"\x00" * pad512)

if __name__ == "__main__":
    write_cpio(sys.argv[1], sys.argv[2])
```

---

## 4. Unpacking the Source Archives

```bash
# Extract ExtremeKRNL
mkdir -p extremekrnl && unzip -q ExtremeKRNL-v2.5_g0s_AOSP_KSU_05-10-2026_03-11-33.zip -d extremekrnl/

# Extract LineageOS boot components
mkdir -p lineage && unzip -q lineage-24.0-20261011-nightly-g0s-signed.zip \
  boot.img vendor_boot.img dtbo.img vendor_dlkm.new.dat.br vendor_dlkm.transfer.list -d lineage/

# Unpack boot.img and vendor_boot.img for both
./unpack_bootimg.py --boot_img extremekrnl/files/boot.img --out out_extreme_boot
./unpack_bootimg.py --boot_img extremekrnl/files/vendor_boot.img --out out_extreme_vendor_boot
./unpack_bootimg.py --boot_img lineage/boot.img --out out_lineage_boot
./unpack_bootimg.py --boot_img lineage/vendor_boot.img --out out_lineage_vendor_boot

# Unpack ExtremeKRNL modules from vendor_ramdisk01
mkdir -p inspect/extreme_vr1
(cd inspect/extreme_vr1 && lz4 -dc ../../out_extreme_vendor_boot/vendor_ramdisk01 | cpio -idm)

# Unpack LineageOS modules from vendor_ramdisk01
mkdir -p inspect/lineage_vr1
(cd inspect/lineage_vr1 && lz4 -dc ../../out_lineage_vendor_boot/vendor_ramdisk01 | cpio -idm)

# Extract LineageOS vendor_dlkm partition
brotli -d lineage/vendor_dlkm.new.dat.br -o lineage/vendor_dlkm.img
mkdir -p inspect/lineage_vendor_dlkm
fsck.erofs --extract=inspect/lineage_vendor_dlkm lineage/vendor_dlkm.img
```

---

## 5. Rebuilding `boot.img`

Combines the `5.10.269-ExtremeKRNL-v2.5+` kernel with LineageOS 24.0's Android 17 init ramdisk:

```bash
./mkbootimg.py \
  --header_version 4 \
  --kernel out_extreme_boot/kernel \
  --ramdisk out_lineage_boot/ramdisk \
  --os_version 17.0.0 \
  --os_patch_level 2026-10 \
  -o new_boot.img
```

---

## 6. Rebuilding `vendor_boot.img`

### Step A: Prepare Ramdisk Fragment 1 (`vendor_ramdisk01`)
1. Copy all 319 `.ko` modules and metadata (`modules.dep`, `modules.alias`, `modules.softdep`) from ExtremeKRNL:
   ```bash
   mkdir -p staging_vr1/lib/modules
   cp -p inspect/extreme_vr1/lib/modules/*.ko staging_vr1/lib/modules/
   cp -p inspect/extreme_vr1/lib/modules/modules.dep staging_vr1/lib/modules/
   cp -p inspect/extreme_vr1/lib/modules/modules.alias staging_vr1/lib/modules/
   cp -p inspect/extreme_vr1/lib/modules/modules.softdep staging_vr1/lib/modules/
   ```
2. Create the sanitized `modules.load` list (using the LineageOS load list, omitting `hid-primax` and early `dhd.ko`):
   ```bash
   grep -v "^hid-primax$" inspect/lineage_vr1/lib/modules/modules.load > staging_vr1/lib/modules/modules.load
   ```
3. Archive to CPIO and compress using **LZ4 legacy format (`-l`)**:
   ```bash
   python3 make_cpio.py staging_vr1 staging_vr1.cpio
   # -l is mandatory: Android kernel requires legacy 0x184c2102 framing
   lz4 -l -12 --favor-decSpeed -f staging_vr1.cpio new_vendor_ramdisk01
   ```

### Step B: Pack `vendor_boot.img` Header v4
```bash
./mkbootimg.py \
  --header_version 4 \
  --pagesize 2048 \
  --base 0x10000000 \
  --kernel_offset 0x00008000 \
  --ramdisk_offset 0x01000000 \
  --tags_offset 0x00000100 \
  --dtb_offset 0x01f00000 \
  --vendor_cmdline " bootconfig" \
  --dtb out_extreme_vendor_boot/dtb \
  --vendor_bootconfig out_lineage_vendor_boot/bootconfig \
  --vendor_ramdisk out_lineage_vendor_boot/vendor_ramdisk00 \
  --ramdisk_type dlkm --ramdisk_name dlkm --vendor_ramdisk_fragment new_vendor_ramdisk01 \
  --vendor_boot new_vendor_boot.img
```

---

## 7. Rebuilding `vendor_dlkm.img`

1. Prepare the staging filesystem:
   ```bash
   mkdir -p staging_vendor_dlkm/etc staging_vendor_dlkm/lib/modules
   cp -p inspect/lineage_vendor_dlkm/etc/* staging_vendor_dlkm/etc/
   cp -p inspect/extreme_vr1/lib/modules/* staging_vendor_dlkm/lib/modules/
   cp -p inspect/lineage_vendor_dlkm/lib/modules/modules.load staging_vendor_dlkm/lib/modules/
   ```
2. Update paths in `modules.dep` to point to `/vendor_dlkm/lib/modules/`:
   ```python
   with open("inspect/extreme_vr1/lib/modules/modules.dep", "r") as f:
       content = f.read().replace("/lib/modules/", "/vendor_dlkm/lib/modules/")
   with open("staging_vendor_dlkm/lib/modules/modules.dep", "w") as f:
       f.write(content)
   ```
3. Build the EROFS filesystem using `lz4hc,12` compression:
   ```bash
   mkfs.erofs -b 4096 -z lz4hc,12 staging_vendor_dlkm_lz4hc.img staging_vendor_dlkm
   ```
4. Pad to the exact partition size (`4806 blocks * 4096 = 19,685,376 bytes`):
   ```python
   TARGET_SIZE = 19685376
   with open("staging_vendor_dlkm_lz4hc.img", "rb") as fin:
       data = fin.read()
   padded = data + b"\x00" * (TARGET_SIZE - len(data))
   with open("new_vendor_dlkm.img", "wb") as fout:
       fout.write(padded)
   ```
5. Brotli compress for LineageOS transfer list packaging:
   ```bash
   brotli -9 new_vendor_dlkm.img -o new_vendor_dlkm.new.dat.br
   ```

---

## 8. Assembling Flashable Packages

### Package 1: Standalone Kernel Zip (`ExtremeKRNL-v2.5_g0s_LineageOS-24.0_KSU.zip`)
```bash
mkdir -p dist_kernel_zip/files dist_kernel_zip/META-INF/com/google/android
cp new_boot.img dist_kernel_zip/files/boot.img
cp new_vendor_boot.img dist_kernel_zip/files/vendor_boot.img
cp extremekrnl/files/dtbo.img dist_kernel_zip/files/dtbo.img
cp new_vendor_dlkm.img dist_kernel_zip/files/vendor_dlkm.img
cp extremekrnl/META-INF/com/google/android/update-binary dist_kernel_zip/META-INF/com/google/android/update-binary
```

Updater script (`dist_kernel_zip/META-INF/com/google/android/updater-script`):
```edify
ui_print("Flashing boot image...");
package_extract_file("files/boot.img", "/dev/block/by-name/boot");

ui_print("Flashing vendor_boot image...");
package_extract_file("files/vendor_boot.img", "/dev/block/by-name/vendor_boot");

ui_print("Flashing dtbo image...");
package_extract_file("files/dtbo.img", "/dev/block/by-name/dtbo");

ui_print("Flashing vendor_dlkm partition...");
package_extract_file("files/vendor_dlkm.img", map_partition("vendor_dlkm"));
unmap_partition("vendor_dlkm");
```

Create the zip:
```bash
(cd dist_kernel_zip && zip -r -9 /Users/danb/Downloads/ExtremeKRNL-v2.5_g0s_LineageOS-24.0_KSU.zip files META-INF)
```

### Package 2: Full Repacked ROM Zip (`lineage-24.0-20261011-nightly-g0s-ExtremeKRNL.zip`)
1. Pad `new_boot.img` to 67,108,864 bytes and `new_vendor_boot.img` to 33,554,432 bytes.
2. Update the existing LineageOS release package:
   ```bash
   cp /Users/danb/Downloads/lineage-24.0-20261011-nightly-g0s-signed.zip /Users/danb/Downloads/lineage-24.0-20261011-nightly-g0s-ExtremeKRNL.zip
   mkdir -p update_rom_staging
   cp padded_boot.img update_rom_staging/boot.img
   cp padded_vendor_boot.img update_rom_staging/vendor_boot.img
   cp new_vendor_dlkm.new.dat.br update_rom_staging/vendor_dlkm.new.dat.br

   # Update entries inside the archive
   (cd update_rom_staging && \
    zip -0 -u /Users/danb/Downloads/lineage-24.0-20261011-nightly-g0s-ExtremeKRNL.zip vendor_dlkm.new.dat.br && \
    zip -u /Users/danb/Downloads/lineage-24.0-20261011-nightly-g0s-ExtremeKRNL.zip boot.img vendor_boot.img)
   ```
