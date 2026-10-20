# Samsung Galaxy S22+ (g0s / SM-S906B) — LineageOS 24.0 Hardware & Kernel Fixes

**Date:** 2026-10-20  
**Target Device:** Samsung Galaxy S22+ (Exynos 2200 / `s5e9925`, codename `g0s`, model `SM-S906B`)  
**Operating System:** LineageOS 24.0 (Android 17 / SDK 37)  
**Kernel:** Linux 5.10.260-g0d060df61c11-dirty (ExtremeKRNL source rebuild with KernelSU Next)

---

## 1. Summary of Issues & Root Causes

### Issue 1: Wi-Fi (`wlan0`) completely broken
- **Root Cause A (Kernel Binary):** The prebuilt `ExtremeKRNL-v2.5` AOSP release was compiled with `CONFIG_R11S_WIRELESS=y` (the Galaxy S23 FE config) instead of `CONFIG_BCM4389=m` + `CONFIG_BCMDHD_PCIE=y`. As a result, the Broadcom PCIe bus glue was compiled out, leaving the kernel physically unable to communicate with the S22+'s BCM4389 Wi-Fi chip.
- **Root Cause B (Vermagic Mismatch):** When flashing prebuilt kernel packages, the kernel vermagic was `5.10.269-ExtremeKRNL-v2.5+`, but the dynamic `/vendor_dlkm` partition still contained stock LineageOS modules compiled for `5.10.260-gd52006865213`. Second-stage init (`wifi_brcm.rc`) executes `modprobe -d /vendor/lib/modules dhd.ko`, which failed with `Exec format error` / vermagic mismatch.
- **Root Cause C (First-Stage Ramdisk Failure):** Broadcom `dhd.ko` cannot be initialized in first-stage init (`vendor_boot` ramdisk) before `/vendor` is mounted, because `dhd.ko` calls `request_firmware()` for `bcmdhd_sta.bin` during device probe. Attempting to probe it in early ramdisk triggers a kernel panic / watchdog reboot (bootloop).

### Issue 2: Fingerprint Sensor ("Something went wrong" / No enrollment prompt)
- **Hardware Architecture:** The S22+ uses a Qualcomm 3D Sonic ultrasonic under-display sensor (`qbt2000` / chipid `QBT2608`).
- **Root Cause A (Dropped Drivers in v2.zip):** In `g0s-fix-wifi-bt-fingerprint-v2.zip`, `fingerprint.ko` and `fingerprint_sysfs.ko` were deleted from `modules.load`, and second-stage loading from `/vendor_dlkm` failed due to the vermagic mismatch described above.
- **Root Cause B (Device Permissions):** When `qbt2000_spidev.ko` is loaded after `on boot`, the character devices `/dev/qbt2000_fd` and `/dev/qbt2000_ipc` were created with default `0600 root:root` permissions. The biometric HAL daemon (`android.hardware.biometrics.fingerprint-service.samsung`, running as UID 1000 `system`) could not open `/dev/qbt2000_fd`, resulting in `BIOMETRIC_ERROR_HW_UNAVAILABLE (Status 7)`.
- **Root Cause C (Touchscreen FOD Disabled):** The Samsung touchscreen controller (`stm_ts_spi`) had its Fingerprint-On-Display (FOD) event routing disabled (`fod:0`). The screen did not send touch/pressure coordinates to the ultrasonic reader.
- **Root Cause D (Missing Template Directory):** The biometric directory `/data/vendor/biometrics/fp/User_0` did not exist on fresh ROM installations, causing template creation in `bauth_FPBAuthServiceStorage` to fail with `fopen is null: 2`.
- **Root Cause E (Sysfs Node Ownership):** `/sys/class/fingerprint/fingerprint/*` nodes were owned by `root:root` instead of `system:radio`.

### Issue 3: KernelSU Next Manager UAPI Mismatch ("Manager version 5 is too low... please upgrade to version 6 or higher")
- **Root Cause:** The kernel was built with KernelSU Next (`next` branch), which incorporated upstream commits (PR #3907 / commit `16be0f72`) bumping the Kernel UAPI version from 5 to 6 (`KERNEL_SU_UAPI_VERSION = 6`). The initial prebuilt Manager APK installed was `v3.4.2 (33355-5)`, which only supported UAPI 5. The manager detected the newer kernel interface and raised a warning banner disabling management features.
- **Resolution:** Upgraded the Manager APK to `v3.4.2-50-gb427a3f9 (33405-6)` and updated `/data/adb/ksud` to `3.4.2-50-gb427a3f9 (uapi: 6)` from the latest KernelSU-Next CI build (`Build Manager CI` run `39297115788`).

### Issue 4: Fingerprint Enrollment Failure Halfway ("Something went wrong. Try again.")
- **Root Cause:** In `drivers/fingerprint/qbt2000_common.c`, the state transition functions (`qbt2000_disable_wuhb`, `qbt2000_enable_wuhb`, `qbt2000_disable_ipc`, `qbt2000_enable_ipc`) returned `-EINVAL` (-22) if the requested state was already active (e.g. disabling WUHB when already disabled). During enrollment, when finger positions or stages transition, the Samsung biometric HAL calls `QFPControl_DeviceEnableInt(0)` (`ioctl(fd, QBT2000_DISABLE_WUHB)`). When the kernel returned `-EINVAL`, `QFPControl_DeviceEnableInt` returned `-1`, prompting the HAL to log `enroll system fail BAuthSensorControl_DeviceEnableInt Enable Error : -1` and abort enrollment with error 8.
- **Resolution:** Patched `fingerprint.ko` at offset `0x2088` (instruction `1dd4: mov w20, #-0x16` -> `2a1f03f4: mov w20, wzr`) so that idempotent enable/disable calls return `0` (Success). Installed the patched module to `/data/adb/modules_fixed/fingerprint.ko` and created missing Trustlet directories `/data/vendor/biometrics/{ta,bk,meta}`.

---

## 2. Implemented Fixes

### A. Kernel Source Rebuild (CI)
Rebuilt the kernel from source specifically for `g0s` under LineageOS 24.0 (`build.yml` run `39306330781`):
- Hard-gated configuration:
  - `CONFIG_BCM4389=m`
  - `CONFIG_BCMDHD_PCIE=y`
  - `CONFIG_BT_BCM43XX=m`
  - `CONFIG_KSU=y` (KernelSU Next `next` branch)
  - `CONFIG_R11S_WIRELESS=n`
  - `CONFIG_CMUCAL_DEBUG=n` (resolved `cmupmucal.ko` link failures)
  - `CONFIG_KASAN=n` & `CONFIG_DEBUG_INFO=n` (resolved 1.84× code bloat so modules fit the 19.6 MB `vendor_dlkm` partition).
- Generated partition images with uniform vermagic `5.10.260-g0d060df61c11-dirty`:
  - `boot.img`
  - `vendor_boot.img`
  - `dtbo.img`
  - `vendor_dlkm.img`

### B. Partition Flashing Protocol
Dynamic partitions (`vendor_dlkm`) inside the `super` container cannot be flashed by standard sideload zips without OTA metadata. All four partitions were flashed cleanly via **fastbootd** (Lineage Recovery → Advanced → Enter fastboot):
```bash
fastboot flash boot boot.img
fastboot flash vendor_boot vendor_boot.img
fastboot flash dtbo dtbo.img
fastboot flash vendor_dlkm vendor_dlkm.img
```

### C. Driver Loading & Hardware Permissions (Multi-Stage Boot Persistence)
To prevent first-stage init ramdisk kernel panics while ensuring drivers are ready before the user or Android framework interacts with hardware:
- **BusyBox vs Android modprobe incompatibility:** KernelSU executes boot scripts via BusyBox ash with `ASH_STANDALONE=1`. Under this environment, BusyBox intercepts bare commands like `modprobe` and dispatches them to its built-in applet, which rejects the `-d <DIR>` argument (`modprobe: invalid option -- d`). All driver load invocations MUST explicitly call `/system/bin/modprobe`.
- **Multi-Stage Orchestration:** The startup script is deployed to three stages (`post-fs-data.d`, `service.d`, and `boot-completed.d`):
  1. `post-fs-data.d`: Loads `dhd.ko`, `fingerprint_sysfs.ko`, and `fingerprint.ko` via `/system/bin/modprobe -d /vendor_dlkm/lib/modules ...`, creates `/dev/qbt2000*` nodes, and applies `0660 system:system` permissions *before* Android HAL services start. This prevents HAL death on initial boot (`HAL deaths since last reboot: 0`).
  2. `service.d`: Acts as a fallback sanity check and verifies permissions.
  3. `boot-completed.d`: Re-verifies FOD state and ensures Wi-Fi is enabled (`cmd wifi set-wifi-enabled enabled`) if Android framework entered `DisabledState` during early boot.
- All actions are logged with timestamps to `/data/adb/load_drivers.log`.

The script:
```bash
#!/system/bin/sh
LOGFILE="/data/adb/load_drivers.log"
exec >> "$LOGFILE" 2>&1
echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] load_drivers ($0, PID $$) ==="

DHD_LOADED_NOW=0
FP_LOADED_NOW=0

# 1. Broadcom Wi-Fi Driver
if ! lsmod | grep -q "^dhd "; then
    echo "Loading dhd.ko..."
    /system/bin/modprobe -d /vendor_dlkm/lib/modules dhd
    if lsmod | grep -q "^dhd "; then
        echo "dhd.ko loaded successfully."
        DHD_LOADED_NOW=1
    else
        echo "Failed to load dhd.ko!"
    fi
else
    echo "dhd.ko already present."
fi

# 2. Fingerprint Drivers
if ! lsmod | grep -q "^fingerprint_sysfs "; then
    echo "Loading fingerprint_sysfs.ko..."
    /system/bin/modprobe -d /vendor_dlkm/lib/modules fingerprint_sysfs
fi

if ! lsmod | grep -q "^fingerprint "; then
    if [ -f /data/adb/modules_fixed/fingerprint.ko ]; then
        echo "Loading patched fingerprint.ko from /data/adb/modules_fixed/..."
        /system/bin/insmod /data/adb/modules_fixed/fingerprint.ko
    else
        echo "Loading stock fingerprint.ko..."
        /system/bin/modprobe -d /vendor_dlkm/lib/modules fingerprint
    fi
    if lsmod | grep -q "^fingerprint "; then
        echo "fingerprint.ko loaded successfully."
        FP_LOADED_NOW=1
    else
        echo "Failed to load fingerprint.ko!"
    fi
else
    echo "fingerprint.ko already present."
fi

# 3. Fix /dev/qbt2000* permissions
for i in 1 2 3 4 5; do
    if [ -e /dev/qbt2000_fd ]; then
        break
    fi
    sleep 0.1
done
if [ -e /dev/qbt2000_fd ] || [ -e /dev/qbt2000_ipc ]; then
    /system/bin/chown system:system /dev/qbt2000* 2>/dev/null || true
    /system/bin/chmod 0660 /dev/qbt2000* 2>/dev/null || true
    echo "/dev/qbt2000* permissions verified."
fi

# 4. Touchscreen FOD mode
if [ -e /sys/class/sec/tsp/cmd ]; then
    echo "fod_enable,1" > /sys/class/sec/tsp/cmd 2>/dev/null || true
    echo "FOD cmd result: $(cat /sys/class/sec/tsp/cmd_result 2>/dev/null || cat /sys/class/sec/tsp/cmd_status 2>/dev/null)"
fi

# 5. Sysfs fingerprint node permissions
if [ -d /sys/class/fingerprint/fingerprint ]; then
    /system/bin/chown system:radio /sys/class/fingerprint/fingerprint/* 2>/dev/null || true
fi

# 6. Biometrics Template & Vendor Directories
mkdir -p /data/vendor/biometrics/fp/User_0
mkdir -p /data/vendor/biometrics/ta
mkdir -p /data/vendor/biometrics/bk
mkdir -p /data/vendor/biometrics/meta
/system/bin/chown -R system:system /data/vendor/biometrics 2>/dev/null || true
/system/bin/chmod -R 0770 /data/vendor/biometrics 2>/dev/null || true
/system/bin/chcon -R u:object_r:biometrics_vendor_data_file:s0 /data/vendor/biometrics 2>/dev/null || true

# 7. Restart HALs ONLY if drivers were newly loaded in this run
if [ "$DHD_LOADED_NOW" = "1" ]; then
    echo "dhd.ko was newly loaded, restarting wifi-service..."
    /system/bin/pkill -f android.hardware.wifi-service || true
fi

if [ "$FP_LOADED_NOW" = "1" ]; then
    echo "fingerprint was newly loaded, restarting fingerprint-service..."
    /system/bin/pkill -f android.hardware.biometrics.fingerprint || true
fi

# 8. Wi-Fi enable watchdog
if [ "$(getprop sys.boot_completed)" = "1" ]; then
    for attempt in 1 2 3 4 5; do
        WIFI_STATUS=$(cmd wifi status 2>/dev/null || true)
        if echo "$WIFI_STATUS" | grep -q "Wifi is disabled"; then
            echo "Wi-Fi is disabled, sending enable command (attempt $attempt)..."
            cmd wifi set-wifi-enabled enabled 2>/dev/null || true
            sleep 2
        elif echo "$WIFI_STATUS" | grep -q "Wifi is enabled"; then
            echo "Wi-Fi is confirmed enabled."
            break
        fi
        sleep 2
    done
fi

echo "=== load_drivers finished ==="
```
Permissions: `chmod 0755 /data/adb/{post-fs-data.d,service.d,boot-completed.d}/load_drivers.sh`

### D. Root Management & KernelSU Next UAPI 6 Alignment
- Installed the matching **KernelSU Next Manager** APK `KernelSU_Next_v3.4.2-50-gb427a3f9_33405-release.apk` (built with UAPI 6 support) directly from GitHub Actions CI run `39297115788`.
- Updated `/data/adb/ksud` to `ksud 3.4.2-50-gb427a3f9 (uapi: 6)` with permissions `0755 root:root`.
- Kernel (`v3.4.2 (33405-6)`), Daemon (`uapi: 6`), and Manager (`v3.4.2-50-gb427a3f9 (33405-6)`) are now perfectly synchronized.
- Preserved a copy of the matching APK under `artifact/KernelSU_Next_v3.4.2-50-gb427a3f9_33405-release.apk`.

---

## 3. Verification & Results

| Subsystem | Test / Verification Command | Result |
|---|---|---|
| **Wi-Fi Link** | `cmd wifi status` | **Connected** to `Beeline_5G_9285` (866 Mbps, RSSI -41 dBm, IP `192.168.0.11`) |
| **Wi-Fi Packets** | `cmd wifi status` (Rx/Tx) | 234k+ Tx, 1.29M+ Rx packets flowing |
| **Bluetooth** | `lsmod \| grep bcm43xx` | `bcm43xx.ko` active, rfkill operational |
| **Fingerprint HAL** | `ps -A \| grep fingerprint` | `android.hardware.biometrics.fingerprint-service.samsung` in `binder_wait_for_work` |
| **TrustZone TEE** | Logcat `tlc_communicate` | `0x00000000 TEEC_SUCCESS` |
| **Sensor Hardware** | Logcat `fps_qbt2000_init` | `fps_qbt2000_init: ret : 0`, model `S90X`, chip `QBT2608` |
| **FOD Routing** | `/sys/class/sec/tsp/cmd_result` | `fod_enable,1:OK` |
| **Enrollment** | `dumpsys fingerprint` | Enrollment client active, fingerprints successfully enrolled |
| **Root Status** | `/data/adb/ksud --version` | `ksud 3.4.2-50-gb427a3f9 (uapi: 6)`, Working (BUILT-IN GKI2, UAPI: 6) |
| **VPN Tunnel** | `dumpsys vpn_management` | Connected, validated, no disconnect loops |
| **VPN Throughput** | `curl -s https://speed.cloudflare.com` | ~3.5 MB/s (~28 Mbps) sustained throughput |
| **TCP Clamping** | `iptables -t mangle -S POSTROUTING` | `TCPMSS --clamp-mss-to-pmtu` active for IPv4 & IPv6 |
| **UFS Cache Affinity**| `/sys/block/sda/queue/rq_affinity` | `2` (local core request completion) |
| **ZRAM Paging** | `/sys/block/zram0/queue/read_ahead_kb` | `0` (zero redundant decompression) |

---

## 7. Ultrasonic Biometrics (QBT2608) Mid-Enrollment Abort Root Cause & Resolution

### 7.1 Double Inversion Bug (`uses_percentage_samples`)
- **Analysis:** Disassembly of `libbauthserver.so` (`FPBAuthService::enroll` @ `0x590ec` - `0x59108`) revealed that Samsung's vendor library naturally computes remaining samples as:
  $$\text{remaining} = 100 - \text{percentage}$$
- Setting `ro.vendor.fingerprint.uses_percentage_samples=true` caused LineageOS's AIDL HAL (`fp-service` @ `0xa83c`) to apply a second subtraction:
  $$\text{remaining}_{\text{HAL}} = 100 - (100 - \text{percentage}) = \text{percentage}$$
- This inverted the progress countdown (reporting increasing remaining touches instead of decreasing to 0), preventing Android framework from ever completing enrollment.
- **Resolution:** Removed `ro.vendor.fingerprint.uses_percentage_samples` and `cancel_on_enroll_completion` from `/data/adb/` startup scripts and live properties.

### 7.2 Qualcomm QFP Circuit Breaker (`ENROLL_FAIL_ON_MAX_REJECTS` / Code 70)
- **Analysis:** Disassembly of Trustlet `00000000-0000-0000-0000-46494e474502` (`handle_do_enroll_stage_4_inject` @ `0x355b0` - `0x35650`) revealed that Qualcomm's `QFP_EnrollInject` has an internal limit on consecutive rejected touches:
  - If a touch has poor acoustic impedance or identical surface features as previous touches, QFP rejects it (`BAuth_Enroll_Do Fail BAD_QUALITY or Enroll Fail : 39`).
  - If 3-4 consecutive rejects occur without a successful capture, `QFP_EnrollInject` triggers `ENROLL_FAIL_ON_MAX_REJECTS` (code `-24`), which maps to return code `70` (`0x46`).
  - Return code 70 aborts the session and triggers Android `errCode=8, VENDOR_ERROR(7)` ("Something went wrong. Try again.").
- **Sensor Technique Requirements:**
  - **Firm, flat contact:** Ultrasonic sensors measure acoustic impedance contrast. Very light or rapid tapping leaves air gaps under ridges.
  - **Hold for ~0.5s:** Allow the transducer array to complete the acoustic sweep before lifting.
  - **Reposition as prompted:** In the second phase (edges/tips), tilt the finger slightly to feed new ridge areas into `QFP_ImageFusion`.

---

## 8. VPN Connectivity, Routing & Kernel Performance/Efficiency Tuning

### 8.1 VPN Stalling, Dropouts & Handshake Timeouts
- **Root Cause A (Path MTU Black Hole):** VPN tunnels (WireGuard, OpenVPN, Cloudflare WARP MASQUE/QUIC) encapsulate network packets, reducing the usable MTU from 1500 bytes to 1280–1420 bytes. Under default Android sysctl `net.ipv4.tcp_mtu_probing = 0`, intermediate hops or tunnel endpoints that drop oversized packets with the Don't Fragment (DF) bit set without returning ICMP Fragmentation Needed create a "black hole". This caused connections to stall or drop after 15–20 seconds.
- **Root Cause B (TCP MSS Mismatch):** When client sockets initiate connections with standard MSS (1460 bytes for IPv4, 1440 for IPv6), encapsulated packets exceed tunnel MTU. Without TCP MSS Clamping, fragmented packets were discarded by strict carrier middleboxes.
- **Root Cause C (Strict Asymmetric Routing Drops):** On dual-connection devices (Wi-Fi + Cellular + VPN), strict reverse-path filtering (`rp_filter = 1`) dropped return packets whose ingress interface differed from the default outbound route.

### 8.2 Performance & Battery Efficiency Bottlenecks
- **Scheduler Accounting Overhead:** `kernel.sched_schedstats = 1` maintained continuous per-task scheduler statistics on every context switch and tick across all 8 cores, wasting CPU cycles in the background.
- **Process Fork Latency:** `kernel.sched_child_runs_first = 0` yielded CPU to the parent process first upon `fork()`, adding measurable latency to app launch animations.
- **UFS 3.1 Interrupt Locality:** `rq_affinity = 1` allowed block I/O completion interrupts to execute on arbitrary CPU cores rather than the requesting core, triggering cross-core cache invalidations and L2/L3 cache misses.
- **ZRAM Decompression Churn:** `read_ahead_kb = 128` forced the kernel to read and decompress 128 KB chunks from ZRAM even when only a 4 KB page was needed. Because ZRAM is compressed RAM with zero seek time, read-ahead only wasted CPU cycles and battery.
- **VFS Cache Premature Reclaim:** Default `vm.vfs_cache_pressure = 100` aggressively reclaimed directory and inode caches, requiring frequent re-reads of filesystem metadata during app switching.

### 8.3 Tuning Implementation
All optimizations are executed automatically on boot via `/data/adb/service.d/load_drivers.sh` and `/data/adb/boot-completed.d/load_drivers.sh`:
```bash
# Network & VPN Packet Flow Fixes
sysctl -w net.ipv4.ip_forward=1
sysctl -w net.ipv6.conf.all.forwarding=1
sysctl -w net.ipv4.tcp_mtu_probing=1
sysctl -w net.ipv4.conf.all.rp_filter=2
sysctl -w net.ipv4.conf.default.rp_filter=2
sysctl -w net.ipv4.tcp_fastopen=3
sysctl -w net.core.rmem_max=8388608
sysctl -w net.core.wmem_max=8388608
sysctl -w net.ipv4.tcp_rmem="4096 87380 8388608"
sysctl -w net.ipv4.tcp_wmem="4096 65536 8388608"

# TCP MSS Clamping for VPN Tunnels
iptables -t mangle -C POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
  iptables -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
ip6tables -t mangle -C POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
  ip6tables -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# Kernel Scheduler & Storage Optimization
sysctl -w kernel.sched_schedstats=0
sysctl -w kernel.sched_child_runs_first=1
sysctl -w vm.vfs_cache_pressure=80
echo 2 > /sys/block/sda/queue/rq_affinity
echo 0 > /sys/block/sda/queue/iostats
echo 128 > /sys/block/sda/queue/nr_requests
echo 0 > /sys/block/zram0/queue/read_ahead_kb
```
