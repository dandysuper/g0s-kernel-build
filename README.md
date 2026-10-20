# Samsung Galaxy S22+ (`g0s`) - LineageOS 24.0 (Android 17) & ExtremeKRNL Guide

A complete, battle-tested guide and toolchain to achieve a **fully functional LineageOS 24.0 (Android 17 / SDK 37)** installation on the **Samsung Galaxy S22+ (Exynos 2200 / `s5e9925`, model `SM-S906B`, codename `g0s`)** with working **Broadcom Wi-Fi**, **Qualcomm Ultrasonic Under-Display Fingerprint (FOD)**, and **KernelSU Next (UAPI 6)**.

---

## 1. Hardware & Software Status

| Component | Status | Details |
| :--- | :---: | :--- |
| **Model** | ✅ | Samsung Galaxy S22+ (`SM-S906B`, Exynos 2200 / `s5e9925`, `g0s`) |
| **Android OS** | ✅ | LineageOS 24.0 (Android 17 / SDK 37) |
| **Kernel** | ✅ | ExtremeKRNL 5.10.260 (`5.10.260-g0d060df61c11-dirty`) |
| **Root Solution** | ✅ | KernelSU Next UAPI 6 (Manager v3.4.2+) |
| **Wi-Fi 6 / 5 GHz** | ✅ | Broadcom BCM4389 (`dhd.ko` via PCIe), persistent across cold/warm reboots |
| **Ultrasonic Biometrics** | ✅ | Qualcomm QBT2608 3D Sonic Gen 2 (`qbt2000` / SPI), 100% enrollment & unlock |
| **Under-Display FOD** | ✅ | Hardware TSP bounding box mapped (`set_fod_rect,426,1675,654,1903`) |
| **Display / Touch** | ✅ | 120 Hz Dynamic AMOLED, capacitive touchscreen (`stm_ts_spi`) |
| **Audio / Calls / BT** | ✅ | Functional |

---

## 2. Core Problems Solved

1. **Broadcom Wi-Fi Drop / Module Loading Failure:**
   - In GKI Android 17, the Broadcom `dhd.ko` PCIe Wi-Fi driver does not load in the proper sequence before `android.hardware.wifi-service` launches, leaving Wi-Fi disabled or unable to turn on.
   - **Fix:** Automated modprobe loading of `dhd.ko` at `post-fs-data` before network daemons start, with a runtime watchdog.

2. **KernelSU Next UAPI 5 vs 6 Incompatibility:**
   - Prebuilt kernels with older KernelSU Next branches expose KernelSU UAPI 5. KernelSU Next Manager v3.4+ displays a warning: *"KernelSU Next manager version is too low / UAPI mismatch, upgrade to version 6"*.
   - **Fix:** Built from source using the latest KernelSU Next `next` branch, providing native **UAPI 6** support matching Manager v3.4.2.

3. **Qualcomm Fingerprint Driver Idempotency Crash (`-EINVAL` / -22):**
   - The stock Samsung `fingerprint.ko` driver returns `-EINVAL` (`-22`) when the AIDL HAL or Trustlet issues duplicate `ENABLE_SPI_CLOCK` or `ENABLE_INTERRUPT` ioctls.
   - **Fix:** Binary patch at offset `0x2088` (`mov w20, wzr`) returning `0` (Success) on duplicate enable requests.

4. **LineageOS AIDL Progress Inversion Bug (`uses_percentage_samples`):**
   - Samsung's vendor biometric library (`libbauthserver.so`) already computes:
     $$\text{remaining} = 100 - \text{percentage}$$
   - Enabling `ro.vendor.fingerprint.uses_percentage_samples=true` caused LineageOS HAL to invert it again:
     $$\text{remaining} = 100 - (100 - \text{percentage}) = \text{percentage}$$
   - This made touches count *upwards* (10, 20, 50 touches remaining), causing Android to register negative progress and preventing remaining touches from ever reaching 0.
   - **Fix:** Cleanly removed `uses_percentage_samples` and `cancel_on_enroll_completion` properties so natural progress countdown occurs.

5. **Qualcomm QFP 3-Reject Circuit Breaker (`ENROLL_FAIL_ON_MAX_REJECTS` / Error 70):**
   - Qualcomm's Trustlet has an internal limit of 3-4 consecutive rejected touches. If a user taps rapidly in the exact same spot without moving their finger, Qualcomm `ImageFusion` rejects the touch (`BAuth_Enroll_Do Fail BAD_QUALITY : 39`). If 3 consecutive rejects occur, QFP aborts the session with error 70 (`Something went wrong. Try again.`).
   - **Fix:** TSP FOD active rectangle configured + proper enrollment technique documented.

---

## 3. Step-by-Step Installation Guide

### Step 1: Install LineageOS 24.0 (Android 17)
1. Ensure your bootloader is unlocked (`OEM unlocking` enabled in Developer Options, unlocked in Download Mode).
2. Reboot to Download Mode (Power off, hold Volume Up + Volume Down and plug into PC).
3. Flash LineageOS 24.0 Recovery using Odin or Heimdall.
4. Boot into Lineage Recovery:
   - Select **Factory Reset** $\to$ **Format data/factory reset**.
5. Select **Apply update** $\to$ **Apply from ADB**:
   ```bash
   adb sideload lineage-24.0-*-UNOFFICIAL-g0s.zip
   ```
6. *(Optional)* Sideload your preferred GApps package (e.g. MindTheGapps for Android 17).

---

### Step 2: Flash Patched ExtremeKRNL (UAPI 6) via fastbootd
> [!IMPORTANT]
> `vendor_dlkm` is a dynamic partition inside the `super` container. It **cannot** be flashed via standard recovery sideload zips. You **must** flash it via **fastbootd**.

1. In Lineage Recovery, navigate to **Advanced** $\to$ **Enter fastboot** (screen will show `fastbootd` in red/yellow text).
2. Connect USB to PC and verify connection:
   ```bash
   fastboot devices
   ```
3. Flash the kernel and vendor partitions:
   ```bash
   fastboot flash boot boot.img
   fastboot flash vendor_boot vendor_boot.img
   fastboot flash dtbo dtbo.img
   fastboot flash vendor_dlkm vendor_dlkm.img
   fastboot reboot
   ```
4. Allow phone to boot into LineageOS 24.0.

---

### Step 3: Install KernelSU Next Manager & Root Verification
1. Download and install **KernelSU Next Manager v3.4.2+** APK:
   ```bash
   adb install KernelSU_Next_v3.4.2.apk
   ```
2. Open KernelSU Next Manager and verify:
   - Status: **Working**
   - Mode: **BUILT-IN (GKI2)**
   - UAPI Version: **6**
3. Open **Settings** inside KernelSU Manager and enable **Root** for Shell / ADB if prompted.

---

### Step 4: Patch the Fingerprint Kernel Module (`fingerprint.ko`)
The stock `fingerprint.ko` must be patched to prevent duplicate enable `-EINVAL` (-22) errors:

1. Pull `fingerprint.ko` from the device:
   ```bash
   adb shell "su -c 'cp /vendor_dlkm/lib/modules/fingerprint.ko /sdcard/'"
   adb pull /sdcard/fingerprint.ko .
   ```
2. Run the included patch script:
   ```bash
   python3 patch_fingerprint.py fingerprint.ko fingerprint_fixed.ko
   ```
   *(Verifies byte replacement at offset `0x2088`: `b4028012` $\to$ `f4031f2a`).*
3. Push the patched module into KernelSU's persistent modules directory:
   ```bash
   adb shell "su -c 'mkdir -p /data/adb/modules_fixed'"
   adb push fingerprint_fixed.ko /sdcard/
   adb shell "su -c 'cp /sdcard/fingerprint_fixed.ko /data/adb/modules_fixed/fingerprint.ko && chmod 0644 /data/adb/modules_fixed/fingerprint.ko'"
   ```

---

### Step 5: Install Driver Loader & Boot Persistence (`load_drivers.sh`)
[`load_drivers.sh`](file:///Users/danb/Documents/kernel/load_drivers.sh) orchestrates the correct loading order for Wi-Fi and fingerprint drivers, configures TSP hardware FOD boundaries, initializes biometrics permissions, and ensures full persistence across cold/warm reboots.

1. Push [`load_drivers.sh`](file:///Users/danb/Documents/kernel/load_drivers.sh) into KernelSU startup stages:
   ```bash
   adb push load_drivers.sh /data/adb/post-fs-data.d/load_drivers.sh
   adb push load_drivers.sh /data/adb/service.d/load_drivers.sh
   adb push load_drivers.sh /data/adb/boot-completed.d/load_drivers.sh
   adb shell "su -c 'chmod 0755 /data/adb/*/*.sh'"
   ```
2. Trigger execution immediately (without rebooting):
   ```bash
   adb shell "su -c 'sh /data/adb/post-fs-data.d/load_drivers.sh'"
   ```

---

### Step 6: Ultrasonic Fingerprint Enrollment Technique
The S22+ features a **Qualcomm 3D Sonic Gen 2 ultrasonic sensor** (not an optical sensor). Ultrasonic waves map the acoustic impedance of ridges in direct contact with the glass.

1. Open **Settings** $\to$ **Security & Privacy** $\to$ **Fingerprint Unlock** $\to$ **Add Fingerprint**.
2. **Firm, Flat Contact:** Press your thumb flat and firmly against the fingerprint icon. Do not use feather-light taps; light taps trap air pockets and cause acoustic reflection failures (`BAD_QUALITY`).
3. **Hold for ~0.5 to 1.0 second:** Keep your thumb on the screen until the haptic buzz confirms capture. Do not tap rapidly like a button.
4. **Vary Thumb Angle for Edges:** Once the progress circle reaches ~50%, the UI prompts you to capture edges and tips. Slightly tilt and roll your thumb angle to feed new ridge areas into Qualcomm's `ImageFusion` engine.
5. **Avoid Rapid Duplicate Taps:** Tapping the exact same position 3 times in a row triggers Qualcomm's 3-reject circuit breaker (`ENROLL_FAIL_ON_MAX_REJECTS`), causing `"Something went wrong"`. Always pause slightly and adjust placement if a touch is not accepted.
6. Complete to 100% and test lockscreen unlock.

---

## 4. Verification & Diagnostics

Run these commands to verify that all systems are operational:

### Wi-Fi Verification
```bash
adb shell "cmd wifi status"
# Expected: Wifi is enabled, connected to SSID, IP assigned
adb shell "lsmod | grep dhd"
# Expected: dhd loaded
```

### KernelSU Next UAPI 6 Verification
```bash
adb shell "/data/adb/ksud --version"
# Expected: ksud (uapi: 6), Working (BUILT-IN GKI2, UAPI: 6)
```

### Biometrics & Sensor Verification
```bash
adb shell "cat /sys/class/sec/tsp/cmd_result"
# Expected: set_fod_rect,426,1675,654,1903:OK
adb shell "dumpsys fingerprint | grep -E 'mCurrentFocus|Enrolled'"
# Expected: Fingerprints enrolled and active
```

---

## 5. Repository Tool Reference

| File | Purpose |
| :--- | :--- |
| [`load_drivers.sh`](file:///Users/danb/Documents/kernel/load_drivers.sh) | All-in-one boot persistence script (Wi-Fi, fingerprint, TSP FOD rect, permissions) |
| [`patch_fingerprint.py`](file:///Users/danb/Documents/kernel/patch_fingerprint.py) | Python utility to patch `fingerprint.ko` offset `0x2088` (`mov w20, wzr`) |
| [`build_bt_patch.py`](file:///Users/danb/Documents/kernel/build_bt_patch.py) | Repacks boot/vendor_boot ramdisk module manifests for KernelSU Next |
| [`repack.py`](file:///Users/danb/Documents/kernel/repack.py) | Android boot image unpacking, patching, and repacking engine |
| [`fixes.md`](file:///Users/danb/Documents/kernel/fixes.md) | Exhaustive reverse-engineering documentation, disassembly traces, and root causes |
| [`.github/workflows/build.yml`](file:///.github/workflows/build.yml) | GitHub Actions CI workflow building ExtremeKRNL with KernelSU Next UAPI 6 |
