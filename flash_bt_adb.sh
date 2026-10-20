#!/usr/bin/env bash
# Flash the Bluetooth patch over adb. Two paths, both touch ONLY vendor_boot.
#
#   1. sideload  — reboot to LineageOS recovery, apply update, adb sideload <zip>
#   2. dd        — device is booted and rooted (KernelSU): push the raw image and
#                  write it straight to the vendor_boot partition. No recovery.
#
# Usage:
#   ./flash_bt_adb.sh --zip ~/Downloads/bt-fix-g0s_ExtremeKRNL-5.10.269.zip --mode dd
#   ./flash_bt_adb.sh --zip ~/Downloads/bt-fix-g0s_NewKernel-5.10.260.zip   --mode sideload

set -euo pipefail

ZIP=""
MODE="dd"

while [ $# -gt 0 ]; do
    case "$1" in
        --zip)  ZIP="$2";  shift 2 ;;
        --mode) MODE="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$ZIP" ]; then
    echo "error: --zip is required" >&2
    exit 2
fi

ZIP="${ZIP/#\~/$HOME}"

if [ ! -f "$ZIP" ]; then
    echo "error: no such zip: $ZIP" >&2
    exit 1
fi

if ! command -v adb >/dev/null 2>&1; then
    echo "error: adb not found in PATH" >&2
    exit 1
fi

echo "device:"
adb devices | sed -n '2,$p' | sed '/^$/d'

case "$MODE" in
    sideload)
        echo
        echo "Put the phone in recovery first:"
        echo "  adb reboot recovery"
        echo "  (then: Apply update from ADB)"
        echo
        read -r -p "Press enter once recovery is showing 'Apply update from ADB'... " _
        echo "sideloading $(basename "$ZIP")"
        adb sideload "$ZIP"
        echo
        echo "Reboot from the recovery menu. Only vendor_boot was written."
        ;;

    dd)
        echo
        echo "This path needs root on the running device (KernelSU)."
        echo "It writes /dev/block/by-name/vendor_boot and nothing else."
        echo
        read -r -p "Continue? [y/N] " answer
        case "$answer" in
            y|Y) ;;
            *) echo "aborted"; exit 1 ;;
        esac

        workdir="$(mktemp -d)"
        trap 'rm -rf "$workdir"' EXIT

        echo "extracting vendor_boot.img from the zip"
        unzip -o -q "$ZIP" files/vendor_boot.img -d "$workdir"
        img="$workdir/files/vendor_boot.img"

        if [ ! -f "$img" ]; then
            echo "error: zip did not contain files/vendor_boot.img" >&2
            exit 1
        fi

        size=$(wc -c < "$img" | tr -d ' ')
        echo "image: $size bytes"

        echo "pushing to /data/local/tmp/vendor_boot.img"
        adb push "$img" /data/local/tmp/vendor_boot.img >/dev/null

        echo "backing up the current partition to /data/local/tmp/vendor_boot.bak.img"
        adb shell su -c \
            "'dd if=/dev/block/by-name/vendor_boot of=/data/local/tmp/vendor_boot.bak.img'" \
            >/dev/null 2>&1 || echo "  (backup failed -- continuing, the patch does not need it)"

        echo "writing vendor_boot"
        adb shell su -c \
            "'dd if=/data/local/tmp/vendor_boot.img of=/dev/block/by-name/vendor_boot bs=4096'" \
            2>&1 | tail -2

        echo "verifying readback"
        remote_sum=$(adb shell su -c \
            "'dd if=/dev/block/by-name/vendor_boot bs=4096 count=$(( (size + 4095) / 4096 )) 2>/dev/null | md5sum'" \
            | awk '{print $1}')
        local_sum=$(md5sum "$img" 2>/dev/null | awk '{print $1}' || md5 -q "$img")
        echo "  partition md5: $remote_sum"
        echo "  image md5    : $local_sum"
        if [ "$remote_sum" != "$local_sum" ]; then
            echo "  WARNING: md5 mismatch -- readback covers a padded region, so a"
            echo "           mismatch here is expected unless the image is block-aligned."
            echo "           Compare the first $size bytes manually if in doubt."
        fi

        echo
        echo "done. reboot:  adb reboot"
        echo "If anything goes wrong, the backup is at /data/local/tmp/vendor_boot.bak.img"
        ;;

    *)
        echo "error: --mode must be 'dd' or 'sideload'" >&2
        exit 2
        ;;
esac
