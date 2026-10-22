# Samsung Galaxy S22+ (`g0s`) - Official LineageOS 24.0 (Android 17) & ExtremeKRNL Guide

A complete, battle-tested guide and toolchain to achieve a **fully functional Official LineageOS 24.0 (Android 17 / SDK 37)** installation on the **Samsung Galaxy S22+ (Exynos 2200 / `s5e9925`, model `SM-S906B`, codename `g0s`)** with working **Broadcom Wi-Fi**, **Qualcomm Ultrasonic Under-Display Fingerprint (FOD)**, and **KernelSU Next (UAPI 6)**.

All pre-patched, ready-to-flash partition images and modules are included directly in the [`kernel_files/`](kernel_files/) folder.

---

## 1. Hardware & Software Status

| Component | Status | Details |
| :--- | :---: | :--- |
| **Model** | ✅ | Samsung Galaxy S22+ (`SM-S906B`, Exynos 2200 / `s5e9925`, `g0s`) |
| **Android OS** | ✅ | **Official** LineageOS 24.0 (Android 17 / SDK 37) |
| **Kernel** | ✅ | ExtremeKRNL 5.10.260 (`5.10.260-g0d060df61c11-dirty`) |
| **Root Solution** | ✅ | KernelSU Next UAPI 6 (Manager v3.4.2+) |
| **Wi-Fi 6 / 5 GHz** | ✅ | Broadcom BCM4389 (`dhd.ko` via PCIe), persistent across cold/warm reboots |
| **Ultrasonic Biometrics** | ✅ | Qualcomm QBT2608 3D Sonic Gen 2 (`qbt2000` / SPI), 100% enrollment & unlock |
| **Under-Display FOD** | ✅ | Hardware TSP bounding box mapped (`set_fod_rect,426,1675,654,1903`) |
| **Display / Touch** | ✅ | 120 Hz Dynamic AMOLED, capacitive touchscreen (`stm_ts_spi`) |
| **Audio / Calls / BT** | ✅ | Functional |
| **VPN & Tunnels** | ✅ | WireGuard, OpenVPN, Cloudflare WARP (MASQUE/QUIC), TCP MSS clamping, MTU auto-probing |
| **Storage & I/O** | ✅ | Android-managed queue and affinity settings are left unchanged |
| **Memory & efficiency** | Configured | ZRAM swap clustering/read-ahead disabled; battery impact is not benchmarked |

---

## 2. Core Problems Solved

1. **Broadcom Wi-Fi Drop / Module Loading Failure:**
   - In GKI Android 17, the Broadcom `dhd.ko` PCIe Wi-Fi driver does not load in the proper sequence before `android.hardware.wifi-service` launches, leaving Wi-Fi disabled or unable to turn on.
   - **Fix:** Automated modprobe loading of `dhd.ko` at `post-fs-data` before network daemons start, with stage-aware fallback and conditional HAL recovery (without forcing Wi-Fi on).

2. **KernelSU Next UAPI 5 vs 6 Incompatibility:**
   - Prebuilt kernels with older KernelSU Next branches expose KernelSU UAPI 5. KernelSU Next Manager v3.4+ displays a warning: *"KernelSU Next manager version is too low / UAPI mismatch, upgrade to version 6"*.
   - **Fix:** Built from source using the latest KernelSU Next `next` branch, providing native **UAPI 6** support matching Manager v3.4.2.

3. **Qualcomm Fingerprint Driver Idempotency Crash (`-EINVAL` / -22):**
   - The stock Samsung `fingerprint.ko` driver returns `-EINVAL` (`-22`) when the HAL repeats an IPC or wake-up (WUHB) interrupt enable/disable ioctl for a state that is already set, which aborts enrollment halfway.
   - **Fix:** Compiled into `fingerprint.ko` by CI builds via [`patches/0001-fingerprint-qbt2000-idempotent-irq-toggles.patch`](patches/0001-fingerprint-qbt2000-idempotent-irq-toggles.patch): repeated requests return `0` (Success) while the driver still skips the duplicate interrupt change. The older `kernel_files/` module uses the equivalent binary patch at offset `0x2088` (`mov w20, wzr`).

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

6. **VPN Drops & Path MTU Black Holes:**
   - Under standard Android 17 network settings, VPN tunnels encapsulating packets reduce the usable MTU below 1500 bytes. With `net.ipv4.tcp_mtu_probing = 0` and un-clamped TCP MSS, packets with the Don't Fragment (DF) bit set are silently discarded by intermediate routers, causing VPNs to stall, timeout, or drop connections.
   - **Fix:** Automated PLPMTUD (`net.ipv4.tcp_mtu_probing = 1`) and IPv4 & IPv6 TCP MSS Clamping (`--clamp-mss-to-pmtu`). Forwarding, socket buffers and reverse-path filtering stay under Android's control (see [fixes.md §8.3](fixes.md#83-tuning-implementation)).

7. **Conservative RAM Management & Efficiency:**
   - **Fix:** When ZRAM is present, set `vm.page-cluster = 0` and disable ZRAM block read-ahead to avoid speculative swap-in work. Android retains control of swappiness and ZRAM sizing; scheduler, CPU governor, VFS cache, and storage queue policy are not overridden. Reduced CPU work and battery use are potential benefits, not measured results.

---

## 3. Step-by-Step Installation Guide

### Step 1: Install Official LineageOS 24.0 (Android 17)
1. Ensure your bootloader is unlocked (`OEM unlocking` enabled in Developer Options, unlocked in Download Mode).
2. Reboot to Download Mode (Power off, hold Volume Up + Volume Down and plug into PC).
3. Flash Official LineageOS 24.0 Recovery using Odin or Heimdall.
4. Boot into Lineage Recovery:
   - Select **Factory Reset** $\to$ **Format data/factory reset**.
5. Select **Apply update** $\to$ **Apply from ADB**:
   ```bash
   adb sideload lineage-24.0-*-g0s.zip
   ```
6. *(Optional)* Sideload your preferred GApps package (e.g. MindTheGapps for Android 17).

---

### Step 2: Flash Patched ExtremeKRNL (UAPI 6) via fastbootd
Pre-built, patched partition images are provided in the [`kernel_files/`](kernel_files/) folder:
- `kernel_files/boot.img` (ExtremeKRNL 5.10.260 with KernelSU Next UAPI 6)
- `kernel_files/vendor_boot.img`
- `kernel_files/dtbo.img`
- `kernel_files/vendor_dlkm.img`

> [!IMPORTANT]
> `vendor_dlkm` is a dynamic partition inside the `super` container. It **cannot** be flashed via standard recovery sideload zips. You **must** flash it via **fastbootd**.

1. In Lineage Recovery, navigate to **Advanced** $\to$ **Enter fastboot** (screen will show `fastbootd` in red/yellow text).
2. Connect USB to PC and verify connection:
   ```bash
   fastboot devices
   ```
3. Flash the kernel and vendor partitions from `kernel_files/`:
   ```bash
   cd kernel_files
   fastboot flash boot boot.img
   fastboot flash vendor_boot vendor_boot.img
   fastboot flash dtbo dtbo.img
   fastboot flash vendor_dlkm vendor_dlkm.img
   fastboot reboot
   ```
4. Allow phone to boot into Official LineageOS 24.0.

---

### Step 3: Install KernelSU Next Manager & Root Verification
1. Download and install **KernelSU Next Manager v3.4.2+** from [KernelSU Next Releases](https://github.com/KernelSU-Next/KernelSU-Next/releases):
   ```bash
   adb install KernelSU_Next_v3.4.2*.apk
   ```
2. Open KernelSU Next Manager and verify:
   - Status: **Working**
   - Mode: **BUILT-IN (GKI2)**
   - UAPI Version: **6**
3. Open **Settings** inside KernelSU Manager and enable **Root** for Shell / ADB if prompted.

---

### Step 4: Fingerprint Driver Fix (`fingerprint.ko`)
**Skip this step for CI builds whose `build-info.txt` lists `source_patch=0001-fingerprint-qbt2000-idempotent-irq-toggles.patch`: their `fingerprint.ko` already contains the fix.** Older builds, including the images in `kernel_files/`, need the separate pre-patched driver `kernel_files/fingerprint_fixed.ko` (offset `0x2088` patched to `mov w20, wzr`).

1. For older builds only, push the pre-patched module into KernelSU's persistent directory:
   ```bash
   adb shell "su -c 'mkdir -p /data/adb/modules_fixed'"
   adb push kernel_files/fingerprint_fixed.ko /sdcard/
   adb shell "su -c 'cp /sdcard/fingerprint_fixed.ko /data/adb/modules_fixed/fingerprint.ko && chmod 0644 /data/adb/modules_fixed/fingerprint.ko'"
   ```
2. After flashing a build that includes the fix, remove the override so the built-in module is used:
   ```bash
   adb shell "su -c 'rm -f /data/adb/modules_fixed/fingerprint.ko'"
   ```
   If an old override is left behind and the new kernel rejects it, `load_drivers.sh` falls back to the `vendor_dlkm` module from the service stage onward.

*(Note: `python3 patch_fingerprint.py <fingerprint.ko> <output.ko>` only patches the older `5.10.260-g0d060df61c11-dirty` module layout and refuses anything else, including modules that already contain the source fix.)*

---

### Step 5: Install Driver Loader & Boot Persistence (`load_drivers.sh`)
[`load_drivers.sh`](load_drivers.sh) orchestrates the correct loading order for Wi-Fi and fingerprint drivers, configures TSP hardware FOD boundaries, initializes biometrics permissions, and ensures full persistence across cold/warm reboots.

The three stages remain necessary: early driver insertion, late fallback, and
post-boot FOD/permission verification. The script infers its stage from its
installed path. Duplicate successful invocations in the same stage are skipped;
failed repairs remain retryable. There is no global “already ran” guard.

**For any future installation:** follow the [validated deployment and recovery
record](docs/boot-cleanup-deployment.md). Verify the single intended device,
root shell, current scripts and required tools first. Back up all existing stage
copies on both host and phone, outside startup directories, including a
`boot-completed.d` copy if present; verify hashes and ownership/mode/SELinux label.
Stage and syntax/hash-check **all** replacements before using atomic renames on
the same filesystem while no loader is running. Preserve metadata and retain a
verified rollback. Do not overwrite active files with a partial direct push or
mix old/new copies (the old script ignores locks).

The initial phone installation retained its exact older tuning block. The user
subsequently authorized PR #1 conservative tuning; the linked record separates
the initial variant from the follow-up canonical-script deployment and hashes.

The loader requires Android `/system/bin/flock` with `-n` and FD locking,
`pgrep`, and `pkill`, plus the existing module tools and KernelSU `ksud`.
Both lock calls explicitly inherit FD 9 (`9>&9`), required by Android mksh and
compatible with KernelSU ash. Validate locking, not only shell syntax; the
opt-in `tests/probe_android_lock.sh` checks both Android shells using only a new
backup-directory lock, never the loader or hardware repairs.
Verify those tools on the target before installation. Early boot does not wait
for the lock or for Android; late contention is bounded. Failed lock acquisition
leaves hardware untouched and is retried by a later stage/manual invocation.

For an explicitly authorized live repair, use the late stage, not the early path:

```bash
# Assumes adb shell id confirms an already-root shell, as on the recorded phone.
adb shell /system/bin/sh /data/adb/service.d/load_drivers.sh
```

Do not run this merely to validate file installation: it performs live hardware
repairs. On a non-root ADB shell use the separately authorized root mechanism;
`su` is not present on every rooted setup.

This does **not** force Wi-Fi on. It preserves driver and HAL recovery without
overriding an intentional off choice. Enable Wi-Fi in Settings if desired.
Fingerprint patched-module preference, both qbt node permissions, FOD rectangle,
biometrics directories/labels and incompatible-property removals remain intact.
HALs are not restarted just because the script ran again.

State lives under `/data/adb/load_drivers-state/<boot-id>/`; successful repairs
are tracked per stage, not globally. Failed repairs and failed HAL restarts
remain retryable. To deliberately reapply a completed stage after external
changes, remove only its `early.repairs`, `service.repairs` or
`completed.repairs` marker while no loader is running, then invoke that stage.
Do not remove the active `lock` file or whole active state directory.
A reboot naturally uses fresh state. Keep old boot directories until a safe
maintenance window; they do not affect the new boot.

**Rollback:** restore all backed-up startup copies together while no loader is
running, retaining the patched fingerprint module. Rollback to the previous
script also restores its Wi-Fi-forcing watchdog. No kernel rebuild or partition
flash is needed for this script-only change. Actual boot, Wi-Fi/BT operation,
enrollment and unlock still require separate device validation.

Host regression checks (never execute the Android script directly on a host):

```bash
python3 -m unittest discover -s tests -v
sh -n load_drivers.sh
git diff --check
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

## 5. Repository File Reference

| File / Folder | Purpose |
| :--- | :--- |
| [`kernel_files/`](kernel_files/) | Pre-built partition images (`boot.img`, `vendor_boot.img`, `dtbo.img`, `vendor_dlkm.img`), and patched `fingerprint_fixed.ko` for those images |
| [`patches/`](patches/) | Kernel source patches applied by CI before compiling (fingerprint IPC/WUHB fix) |
| [`load_drivers.sh`](load_drivers.sh) | All-in-one boot persistence script (Wi-Fi, fingerprint, TSP FOD rect, permissions) |
| [`patch_fingerprint.py`](patch_fingerprint.py) | Binary fix (`0x2088`, `mov w20, wzr`) for the older `kernel_files/` module only |
| [`build_bt_patch.py`](build_bt_patch.py) | Repacks boot/vendor_boot ramdisk module manifests for KernelSU Next |
| [`repack.py`](repack.py) | Android boot image unpacking, patching, and repacking engine |
| [`fixes.md`](fixes.md) | Exhaustive reverse-engineering documentation, disassembly traces, and root causes |
| [`.github/workflows/build.yml`](.github/workflows/build.yml) | GitHub Actions CI workflow building ExtremeKRNL with KernelSU Next UAPI 6 |
