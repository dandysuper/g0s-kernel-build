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

# 4. Touchscreen FOD Mode & Hardware Coordinates (SM-S906B: 540|1789|114)
if [ -e /sys/class/sec/tsp/cmd ]; then
    echo "fod_enable,1" > /sys/class/sec/tsp/cmd 2>/dev/null || true
    echo "set_fod_rect,426,1675,654,1903" > /sys/class/sec/tsp/cmd 2>/dev/null || true
    echo "FOD cmd result: $(cat /sys/class/sec/tsp/cmd_result 2>/dev/null || cat /sys/class/sec/tsp/cmd_status 2>/dev/null)"
fi

# 5. Sysfs fingerprint node permissions
if [ -d /sys/class/fingerprint/fingerprint ]; then
    /system/bin/chown system:radio /sys/class/fingerprint/fingerprint/* 2>/dev/null || true
fi

# 6. Biometrics Template, Metadata & Vendor Directories
mkdir -p /data/vendor/biometrics/fp/User_0
mkdir -p /data/vendor/biometrics/ta
mkdir -p /data/vendor/biometrics/bk
mkdir -p /data/vendor/biometrics/meta
mkdir -p /data/vendor/biometrics/log
mkdir -p /data/vendor/misc/qti_fp/bg_estimation
mkdir -p /data/vendor/misc/qti_fp/calib_test
mkdir -p /data/vendor/misc/qti_fp/template
/system/bin/chown -R system:system /data/vendor/biometrics 2>/dev/null || true
/system/bin/chmod -R 0770 /data/vendor/biometrics 2>/dev/null || true
/system/bin/chcon -R u:object_r:biometrics_vendor_data_file:s0 /data/vendor/biometrics 2>/dev/null || true
/system/bin/chown -R system:system /data/vendor/misc/qti_fp 2>/dev/null || true
/system/bin/chmod -R 0770 /data/vendor/misc/qti_fp 2>/dev/null || true

# 7. Configure Fingerprint AIDL HAL Properties (Hardware coordinates)
# Note: uses_percentage_samples must NOT be set on SM-S906B (libbauthserver natively computes remaining = 100 - percentage)
if [ -x /data/adb/ksud ]; then
    /data/adb/ksud resetprop -d ro.vendor.fingerprint.uses_percentage_samples 2>/dev/null || true
    /data/adb/ksud resetprop -d ro.vendor.fingerprint.cancel_on_enroll_completion 2>/dev/null || true
fi

# 8. Restart HALs ONLY if drivers were newly loaded in this run
if [ "$DHD_LOADED_NOW" = "1" ]; then
    echo "dhd.ko was newly loaded, restarting wifi-service..."
    /system/bin/pkill -f android.hardware.wifi-service || true
fi

if [ "$FP_LOADED_NOW" = "1" ]; then
    echo "fingerprint was newly loaded, restarting fingerprint-service..."
    /system/bin/pkill -f android.hardware.biometrics.fingerprint || true
fi

# 9. Wi-Fi enable watchdog
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

# 10. Network & VPN Protocol Tuning (Eliminates PMTU black-holes, enables packet forwarding, clamps TCP MSS)
echo "Applying Network & VPN optimizations..."
/system/bin/sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true
/system/bin/sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null 2>&1 || true
/system/bin/sysctl -w net.ipv4.tcp_mtu_probing=1 >/dev/null 2>&1 || true
/system/bin/sysctl -w net.ipv4.conf.all.rp_filter=2 >/dev/null 2>&1 || true
/system/bin/sysctl -w net.ipv4.conf.default.rp_filter=2 >/dev/null 2>&1 || true
/system/bin/sysctl -w net.ipv4.tcp_fastopen=3 >/dev/null 2>&1 || true
/system/bin/sysctl -w net.core.rmem_max=8388608 >/dev/null 2>&1 || true
/system/bin/sysctl -w net.core.wmem_max=8388608 >/dev/null 2>&1 || true
/system/bin/sysctl -w net.ipv4.tcp_rmem="4096 87380 8388608" >/dev/null 2>&1 || true
/system/bin/sysctl -w net.ipv4.tcp_wmem="4096 65536 8388608" >/dev/null 2>&1 || true

# iptables TCPMSS clamping (prevents VPN packets exceeding tunnel MTU from being silently dropped)
iptables -t mangle -C POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || iptables -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
ip6tables -t mangle -C POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || ip6tables -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true

# 11. Conservative memory and battery-efficiency tuning
echo "Applying conservative memory & efficiency tuning..."
# Scheduler statistics are diagnostic accounting, not a scheduling policy.
/system/bin/sysctl -w kernel.sched_schedstats=0 >/dev/null 2>&1 || true

# Avoid clustered swap-in and block read-ahead for compressed RAM. Leave Android's
# swappiness, ZRAM sizing, CPU governor, and storage queue policy untouched.
if [ -d /sys/block/zram0 ]; then
    /system/bin/sysctl -w vm.page-cluster=0 >/dev/null 2>&1 || true
    if [ -w /sys/block/zram0/queue/read_ahead_kb ]; then
        echo 0 > /sys/block/zram0/queue/read_ahead_kb 2>/dev/null || true
    fi
fi

echo "=== load_drivers finished ==="
