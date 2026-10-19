# Handoff Report: ExtremeKRNL Port to LineageOS 24.0 (Samsung Galaxy S22+ / `g0s`)

## 1. Executive Summary

- **Device**: Samsung Galaxy S22+ (Exynos 2200, `s5e9925`, SM-S906B, codename `g0s`)
- **ROM Target**: LineageOS 24.0 Nightly (`lineage-24.0-20261011-nightly-g0s-signed.zip`, Android 17 / SDK 37)
- **Custom Kernel**: ExtremeKRNL v2.5 (`ExtremeKRNL-v2.5_g0s_AOSP_KSU_05-10-2026_03-11-33.zip`, Linux 5.10.269 + KernelSU)
- **Current Operational Status**:
  - **Boot & Init**: **Working** (Resolved OneUI Android 12 init conflict).
  - **Storage & Encryption**: **Working** (Restored LineageOS wrapped-key fstab and `/system_ext` mount).
  - **Mobile Data (Cellular)**: **Working** (Modem IPC via `cpif.ko` operates correctly).
  - **Bluetooth**: **Resolved** in rebuilt ramdisk (Unblocked first-stage `libmodprobe` failure that prevented `bcm43xx.ko` loading).
  - **Wi-Fi**: **Broken in current kernel binary** — Root cause identified: the kernel was compiled with `CONFIG_R11S_WIRELESS=y` (Galaxy S23 FE config) with `CONFIG_BCM4389=n` and `CONFIG_BCMDHD_PCIE` unset.

---

## 2. Issues Discovered and Solutions Applied

### A. Boot & Storage Failures (Solved)
1. **Init Ramdisk Mismatch**:
   - *Problem*: The original `ExtremeKRNL` zip packaged a Samsung OneUI Android 12 ramdisk (`SP1A.210812.016`, SDK 31). Flashing it directly over LineageOS 24.0 (Android 17 / SDK 37) broke first-stage init and wiped out LineageOS's `/system/bin` utilities (`modprobe`, `toolbox`, `getprop`) and runtime libraries (`system/lib64/*`).
   - *Fix*: Created a new `boot.img` combining the `5.10.269-ExtremeKRNL-v2.5+` kernel with LineageOS 24.0's native Android 17 init ramdisk.
2. **Fstab Corruption & Encryption Breakdown**:
   - *Problem*: ExtremeKRNL replaced `vendor_boot`'s `vendor_ramdisk00` with Samsung OneUI partitions (`/prism`, `/optics`, `/harx`, `/keystorage`), completely omitting `/system_ext` and stripping the `fileencryption=...wrappedkey_v0` and `metadata_encryption` flags from `/data`.
   - *Fix*: Preserved LineageOS 24.0's original `vendor_ramdisk00` containing the proper `fstab.s5e9925` and ramdisk binaries.

### B. The Bluetooth Breakdown (Solved)
- *Problem*: In Android's first-stage init (`first_stage_init.cpp`), module loading runs in strict mode (`strict = true`). If any module in `modules.load` fails to load, `libmodprobe` **immediately aborts execution and skips all subsequent modules**.
- In the original ExtremeKRNL build:
  - `dhd.ko` (Wi-Fi) was listed at **line 127**.
  - Because `/vendor` is not mounted during first-stage init, `dhd.ko` could not access firmware files, failing to probe.
  - The strict loop aborted at line 127.
  - **Line 180 (`bcm43xx.ko` — the Broadcom Bluetooth power/rfkill driver) was never reached**, leaving Bluetooth entirely dead.
- *Fix*: Reconstructed `vendor_ramdisk01` with LineageOS's upstream `g0s.load` list (excluding `dhd.ko`, `fingerprint.ko`, `fingerprint_sysfs.ko`, `input_booster_lkm.ko`, and `sec_debug_coredump.ko` from early ramdisk loading). This allows `bcm43xx.ko` to load cleanly and register the `/sys/class/rfkill/` device.

### C. The Wi-Fi Failure (Root Cause Identified)
- *Symptom*: Mobile data works, but Wi-Fi cannot turn on or find networks.
- *Investigation*:
  - Extracted and decompressed the runtime kernel configurations from `/proc/config.gz` (`IKCONFIG`) for both LineageOS stock and ExtremeKRNL:
    ```ini
    # LineageOS Stock Kernel (Wi-Fi Works)
    CONFIG_BCM4389=m
    CONFIG_BCMDHD_PCIE=y
    # CONFIG_R11S_WIRELESS is not set

    # ExtremeKRNL v2.5 (g0s build - Wi-Fi Broken)
    # CONFIG_BCM4389 is not set
    # CONFIG_BCMDHD_PCIE is not set
    CONFIG_R11S_WIRELESS=y
    ```
- *Diagnosis*:
  - The Samsung Galaxy S22+ (`g0s`) utilizes the **Broadcom BCM4389** Wi-Fi chip connected over **PCIe** (`CONFIG_BCM4389=m`, `CONFIG_BCMDHD_PCIE=y`).
  - The builder of ExtremeKRNL accidentally built the kernel with the config for the **Galaxy S23 FE (`r11s`)** (`CONFIG_R11S_WIRELESS=y`), disabling `CONFIG_BCM4389` and `CONFIG_BCMDHD_PCIE`.
  - Because the PCIe glue and BCM4389 chip driver are completely disabled inside the pre-compiled `ExtremeKRNL` binary kernel image, the `dhd.ko` module cannot bind to the hardware PCIe bus on the S22+.

---

## 3. Artifacts and Generated Packages

The following packages were created and verified in `/Users/danb/Downloads/`:

| Package Path | Size | Description |
|---|---|---|
| [`ExtremeKRNL-v2.5_g0s_LineageOS-24.0_KSU.zip`](file:///Users/danb/Downloads/ExtremeKRNL-v2.5_g0s_LineageOS-24.0_KSU.zip) | 39 MB | Standalone flashable kernel zip for recovery. Contains updated `boot.img`, `vendor_boot.img`, `dtbo.img`, and dynamic `vendor_dlkm.img`. |
| [`lineage-24.0-20261011-nightly-g0s-ExtremeKRNL.zip`](file:///Users/danb/Downloads/lineage-24.0-20261011-nightly-g0s-ExtremeKRNL.zip) | 1.1 GB | Full LineageOS 24.0 ROM package with ExtremeKRNL and KernelSU pre-integrated. |

### Technical Specifications of Repacked Images
- **`boot.img`** (Boot header v4):
  - Kernel: `5.10.269-ExtremeKRNL-v2.5+` (with KernelSU support)
  - Ramdisk: LineageOS 24.0 Android 17 init ramdisk
- **`vendor_boot.img`** (Vendor boot header v4):
  - `vendor_ramdisk00`: LineageOS 24.0 first-stage ramdisk (preserves fstab, binaries, encryption)
  - `vendor_ramdisk01` (`dlkm`): All 319 modules for `5.10.269-ExtremeKRNL-v2.5+` with sanitized `modules.load`
  - DTB: ExtremeKRNL device tree table
- **`vendor_dlkm.img`** (EROFS, 4806 blocks / 19,685,376 bytes):
  - Rebuilt with all modules compiled for `5.10.269-ExtremeKRNL-v2.5+`
  - Dependency paths formatted for `/vendor_dlkm/lib/modules/`

---

## 4. Next Steps for Incoming Agent / Developer

To get Wi-Fi functioning on top of the already fixed Bluetooth, Mobile Data, and Storage:

### Option 1: Recompile the Kernel from Source with Correct S22+ Wi-Fi Config (Recommended)
1. **Kernel Source Repository**:
   - Upstream repo: `https://github.com/ExtremeXT/android_kernel_samsung_s5e9925`
   - Active branch: `lineage-24.0`
2. **Defconfig Adjustments**:
   - Ensure the config includes `g0s.config` instead of `r11s.config`.
   - Verify the following configuration options are set:
     ```ini
     CONFIG_BCM4389=m
     CONFIG_BCMDHD_PCIE=y
     # CONFIG_R11S_WIRELESS is not set
     CONFIG_BT_BCM43XX=m
     ```
3. **Build & Package**:
   - Compile using Android Clang (`aarch64-linux-gnu-` / LLVM).
   - Replace `kernel` in [`new_boot.img`](file:///Users/danb/Documents/kernel/dist_kernel_zip/files/boot.img) and update `dhd.ko` in `vendor_boot` and `vendor_dlkm`.

### Option 2: Patch KernelSU Directly into LineageOS 24.0 Stock Kernel
If the user's primary goal with ExtremeKRNL was gaining root via **KernelSU** on LineageOS:
1. LineageOS 24.0's stock kernel (`5.10.260-gd52006865213`) already has 100% working Wi-Fi (BCM4389 over PCIe), Bluetooth, Mobile Data, and camera.
2. KernelSU-Next or KernelSU can be integrated into the stock LineageOS kernel using the standard KSU patch:
   ```bash
   curl -LSs "https://raw.githubusercontent.com/tiann/KernelSU/main/kernel/setup.sh" | bash -
   ```
3. Building the stock LineageOS kernel with KSU enabled guarantees all hardware drivers (Wi-Fi, Bluetooth, RIL, display) remain functional while providing root.
