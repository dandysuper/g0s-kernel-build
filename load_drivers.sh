#!/system/bin/sh
LOGFILE="/data/adb/load_drivers.log"
exec >> "$LOGFILE" 2>&1
echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] load_drivers ($0, PID $$) ==="

# KernelSU runs identical copies at distinct stages. Never wait for Android from
# post-fs-data; later stages must remain able to finish/retry incomplete repairs.
case "${1:-$0}" in
    early|*/post-fs-data.d/*) STAGE=early ;;
    completed|*/boot-completed.d/*) STAGE=completed ;;
    service|*/service.d/*) STAGE=service ;;
    *) STAGE=service ;;
esac
IFS= read -r BOOT_ID < /proc/sys/kernel/random/boot_id || exit 1
case "$BOOT_ID" in ''|*[!a-zA-Z0-9-]*) exit 1 ;; esac
STATE="/data/adb/load_drivers-state/$BOOT_ID"
umask 077
mkdir -p "$STATE" || exit 1
# FD locks are released on exit/crash and reboot: a leftover file is harmless.
# No PID lock stealing (PID reuse and publication races can admit two owners).
exec 9>"$STATE/lock" || exit 1
# Android mksh needs an explicit inherited redirection for external flock;
# KernelSU BusyBox ash accepts it too. Keep the same open FD for the whole run.
if ! /system/bin/flock -n 9 9>&9; then
    echo "Loader lock busy or /system/bin/flock unavailable ($STAGE)."
    [ "$STAGE" = early ] && exit 0
    LOCKED=0
    for _attempt in 1 2 3 4 5 6 7 8 9 10; do
        sleep 0.1
        if /system/bin/flock -n 9 9>&9; then LOCKED=1; break; fi
    done
    if [ "$LOCKED" != 1 ]; then
        echo "Loader busy or flock unavailable; retry at a later stage/manual run."
        exit 1
    fi
fi

# Sysfs distinguishes a live module from one still loading/unloading without
# spawning lsmod/grep pipelines on every check. Do not cache across insertions.
module_loaded() {
    [ -r "/sys/module/$1/initstate" ] || return 1
    IFS= read -r module_state < "/sys/module/$1/initstate" || return 1
    [ "$module_state" = live ]
}

DHD_LOADED_NOW=0
FP_LOADED_NOW=0
WIFI_WAS_RUNNING=0
FP_WAS_RUNNING=0

# 1. Broadcom Wi-Fi Driver
if ! module_loaded dhd; then
    # A HAL started after module insertion needs no restart.
    /system/bin/pgrep -f android.hardware.wifi-service >/dev/null 2>&1 && WIFI_WAS_RUNNING=1
    echo "Loading dhd.ko..."
    /system/bin/modprobe -d /vendor_dlkm/lib/modules dhd
    if module_loaded dhd; then
        echo "dhd.ko loaded successfully."
        DHD_LOADED_NOW=1
    else
        echo "Failed to load dhd.ko!"
    fi
else
    echo "dhd.ko already present."
fi

# 2. Fingerprint Drivers
if ! module_loaded fingerprint_sysfs; then
    echo "Loading fingerprint_sysfs.ko..."
    /system/bin/modprobe -d /vendor_dlkm/lib/modules fingerprint_sysfs
    rm -f "$STATE/$STAGE.repairs"
fi

if ! module_loaded fingerprint; then
    /system/bin/pgrep -f android.hardware.biometrics.fingerprint >/dev/null 2>&1 && FP_WAS_RUNNING=1
    if [ -f /data/adb/modules_fixed/fingerprint.ko ]; then
        echo "Loading patched fingerprint.ko from /data/adb/modules_fixed/..."
        /system/bin/insmod /data/adb/modules_fixed/fingerprint.ko
    else
        echo "Loading stock fingerprint.ko..."
        /system/bin/modprobe -d /vendor_dlkm/lib/modules fingerprint
    fi
    if module_loaded fingerprint; then
        echo "fingerprint.ko loaded successfully."
        FP_LOADED_NOW=1
        rm -f "$STATE/$STAGE.repairs"
    else
        echo "Failed to load fingerprint.ko!"
    fi
else
    echo "fingerprint.ko already present."
fi

# Each stage verifies hardware once; failed or not-yet-present nodes stay retryable.
if [ ! -f "$STATE/$STAGE.repairs" ]; then
REPAIRS_OK=1
# 3. Fix /dev/qbt2000* permissions (late stages only wait briefly for nodes)
if [ "$STAGE" != early ]; then
for _attempt in 1 2 3 4 5; do
    if [ -e /dev/qbt2000_fd ] && [ -e /dev/qbt2000_ipc ]; then
        break
    fi
    sleep 0.1
done
fi
if [ -e /dev/qbt2000_fd ] || [ -e /dev/qbt2000_ipc ]; then
    /system/bin/chown system:system /dev/qbt2000* 2>/dev/null || REPAIRS_OK=0
    /system/bin/chmod 0660 /dev/qbt2000* 2>/dev/null || REPAIRS_OK=0
fi
# Repair available nodes immediately, but do not cache partial readiness.
if [ ! -e /dev/qbt2000_fd ] || [ ! -e /dev/qbt2000_ipc ]; then
    REPAIRS_OK=0
fi

# 4. Touchscreen FOD Mode & Hardware Coordinates (SM-S906B: 540|1789|114)
if [ -e /sys/class/sec/tsp/cmd ]; then
    for command in "fod_enable,1" "set_fod_rect,426,1675,654,1903"; do
        { echo "$command" > /sys/class/sec/tsp/cmd; } 2>/dev/null || REPAIRS_OK=0
        FOD_RESULT=$(cat /sys/class/sec/tsp/cmd_result 2>/dev/null || cat /sys/class/sec/tsp/cmd_status 2>/dev/null)
        echo "FOD cmd result: $FOD_RESULT"
        case "$FOD_RESULT" in "$command:OK"|OK) ;; *) REPAIRS_OK=0 ;; esac
    done
else
    REPAIRS_OK=0
fi

# 5. Sysfs fingerprint node permissions
if [ -d /sys/class/fingerprint/fingerprint ]; then
    /system/bin/chown system:radio /sys/class/fingerprint/fingerprint/* 2>/dev/null || REPAIRS_OK=0
else
    REPAIRS_OK=0
fi

# 6. Biometrics Template, Metadata & Vendor Directories
# A failed mkdir/permission/label update must not mark the stage complete.
repair() { "$@" || REPAIRS_OK=0; }
repair mkdir -p /data/vendor/biometrics/fp/User_0 /data/vendor/biometrics/ta \
    /data/vendor/biometrics/bk /data/vendor/biometrics/meta /data/vendor/biometrics/log \
    /data/vendor/misc/qti_fp/bg_estimation /data/vendor/misc/qti_fp/calib_test \
    /data/vendor/misc/qti_fp/template
repair /system/bin/chown -R system:system /data/vendor/biometrics 2>/dev/null
repair /system/bin/chmod -R 0770 /data/vendor/biometrics 2>/dev/null
repair /system/bin/chcon -R u:object_r:biometrics_vendor_data_file:s0 /data/vendor/biometrics 2>/dev/null
repair /system/bin/chown -R system:system /data/vendor/misc/qti_fp 2>/dev/null
repair /system/bin/chmod -R 0770 /data/vendor/misc/qti_fp 2>/dev/null

# 7. Configure Fingerprint AIDL HAL Properties (Hardware coordinates)
# Note: uses_percentage_samples must NOT be set on SM-S906B (libbauthserver natively computes remaining = 100 - percentage)
if [ -x /data/adb/ksud ]; then
    repair /data/adb/ksud resetprop -d ro.vendor.fingerprint.uses_percentage_samples 2>/dev/null
    repair /data/adb/ksud resetprop -d ro.vendor.fingerprint.cancel_on_enroll_completion 2>/dev/null
else
    REPAIRS_OK=0
fi
# A later successful module insertion must still get its repair pass.
for module in dhd fingerprint_sysfs fingerprint; do
    module_loaded "$module" || REPAIRS_OK=0
done
if [ "$REPAIRS_OK" = 1 ]; then
    touch "$STATE/$STAGE.repairs"
else
    echo "Hardware repairs incomplete ($STAGE); retry at a later stage/manual run."
fi
fi

# 8. Restart only a HAL that was already running without its driver. Persist a
# failed restart for retry even though the module is now loaded.
if [ "$DHD_LOADED_NOW" = 1 ] && [ "$WIFI_WAS_RUNNING" = 1 ]; then
    touch "$STATE/wifi.restart"
fi
if [ "$FP_LOADED_NOW" = 1 ] && [ "$FP_WAS_RUNNING" = 1 ]; then
    touch "$STATE/fingerprint.restart"
fi
if [ -f "$STATE/wifi.restart" ]; then
    if ! /system/bin/pgrep -f android.hardware.wifi-service >/dev/null 2>&1 ||
        /system/bin/pkill -f android.hardware.wifi-service; then
        rm -f "$STATE/wifi.restart"
    fi
fi
if [ -f "$STATE/fingerprint.restart" ] && [ -f "$STATE/$STAGE.repairs" ]; then
    if ! /system/bin/pgrep -f android.hardware.biometrics.fingerprint >/dev/null 2>&1 ||
        /system/bin/pkill -f android.hardware.biometrics.fingerprint; then
        rm -f "$STATE/fingerprint.restart"
    fi
fi

if [ -f "$STATE/fingerprint.restart" ]; then
    echo "Fingerprint restart pending: repairs incomplete or HAL restart failed."
fi

# 9. Do not force Wi-Fi on: DisabledState alone cannot distinguish a failed
# startup from the user's saved off preference. Driver/HAL repair stays above.

# Hardware recovery is the only work on the blocking post-fs-data path. Keep
# optional tuning and bounded xtables lock waits in asynchronous stages.
if [ "$STAGE" != early ] && [ ! -f "$STATE/tuning" ]; then
    # Do not enable forwarding globally: it resets IPv4 host settings and
    # changes IPv6 router-advertisement behavior. netd owns routing and buffers.
    if [ ! -f "$STATE/tuning.network" ]; then
        if [ ! -e /proc/sys/net/ipv4/tcp_mtu_probing ] ||
            /system/bin/sysctl -w net.ipv4.tcp_mtu_probing=1 >/dev/null 2>&1; then
            touch "$STATE/tuning.network"
        fi
    fi

    # Track families independently so absent IPv6 or a failed rule never
    # replays successful sysctl writes or duplicates the other family's rule.
    for family in ipv4 ipv6; do
        [ -f "$STATE/tuning.mss.$family" ] && continue
        if [ ! -d "/proc/sys/net/$family" ]; then
            touch "$STATE/tuning.mss.$family"
            continue
        fi
        case "$family" in ipv4) firewall=iptables ;; ipv6) firewall=ip6tables ;; esac
        if "$firewall" -w 1 -t mangle -C POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null ||
            "$firewall" -w 1 -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null; then
            touch "$STATE/tuning.mss.$family"
        fi
    done

    # Some production kernels omit scheduler statistics entirely.
    if [ ! -f "$STATE/tuning.scheduler" ]; then
        if [ ! -e /proc/sys/kernel/sched_schedstats ] ||
            /system/bin/sysctl -w kernel.sched_schedstats=0 >/dev/null 2>&1; then
            touch "$STATE/tuning.scheduler"
        fi
    fi

    # A delayed ZRAM device must not cause successful network tuning to repeat.
    # Android retains swappiness, compression, sizing, LMKD and thermal policy.
    if [ ! -f "$STATE/tuning.zram" ] && [ -d /sys/block/zram0 ]; then
        ZRAM_OK=1
        /system/bin/sysctl -w vm.page-cluster=0 >/dev/null 2>&1 || ZRAM_OK=0
        if [ -w /sys/block/zram0/queue/read_ahead_kb ]; then
            echo 0 > /sys/block/zram0/queue/read_ahead_kb 2>/dev/null || ZRAM_OK=0
        else
            ZRAM_OK=0
        fi
        [ "$ZRAM_OK" = 1 ] && touch "$STATE/tuning.zram"
    fi

    if [ -f "$STATE/tuning.network" ] && [ -f "$STATE/tuning.mss.ipv4" ] &&
        [ -f "$STATE/tuning.mss.ipv6" ] && [ -f "$STATE/tuning.scheduler" ] &&
        [ -f "$STATE/tuning.zram" ]; then
        touch "$STATE/tuning"
    fi
fi

echo "=== load_drivers finished ($STAGE) ==="
