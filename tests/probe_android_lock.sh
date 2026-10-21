#!/system/bin/sh
# Opt-in Android lock-only probe. Never execute load_drivers.sh for this check.
# Pass a NEW directory beneath /data/adb/load_drivers-backups; no startup paths.
set -eu
case "$1" in /data/adb/load_drivers-backups/*) ;; *) exit 1 ;; esac
umask 077
mkdir "$1"
lock="$1/lock"
exec 9>"$lock"
/system/bin/flock -n 9 9>&9
# Verify mutual exclusion against a separately opened FD in BOTH shells.
contender='exec 8>"$1"; if /system/bin/flock -n 8 8>&8; then exit 1; fi'
/system/bin/sh -c "$contender" probe "$lock"
ASH_STANDALONE=1 /data/adb/ksu/bin/busybox ash -c "$contender" probe "$lock"
# A child inheriting the SAME open file description must retain the lock.
/system/bin/sh -c '/system/bin/flock -n 9 9>&9' 9>&9
ASH_STANDALONE=1 /data/adb/ksu/bin/busybox ash -c '/system/bin/flock -n 9 9>&9' 9>&9
/system/bin/flock -u 9 9>&9
# Once released, both shells must be able to acquire it independently.
contender='exec 8>"$1"; /system/bin/flock -n 8 8>&8'
/system/bin/sh -c "$contender" probe "$lock"
ASH_STANDALONE=1 /data/adb/ksu/bin/busybox ash -c "$contender" probe "$lock"
echo "LOCK_PROBE_PASSED: acquire, exclude, inherit, release, reacquire in mksh and ash"
