# Boot-action cleanup: verification and device deployment

## Scope and preservation

`load_drivers.sh` is the canonical repository script. The cleanup retains early,
service and completed-stage repairs, explicit Android module tools, patched
fingerprint preference/stock fallback, both qbt node permissions, fingerprint
sysfs ownership, FOD enable and rectangle `426,1675,654,1903`, biometrics/QTI
paths/permissions/SELinux label and the two fingerprint property deletions.
Bluetooth module manifests and kernel/module images are untouched.

A kernel FD lock serializes stages. Early never sleeps or waits for the lock.
Late contention is bounded; missing drivers, nodes, rejected FOD commands,
failed permission changes and failed HAL restarts remain retryable. Successful
repairs are cached separately for each stage; fingerprint insertion invalidates
that stage's cached repairs. Available qbt nodes are repaired immediately even
if the second node has not appeared. Only a HAL already running before a newly
inserted driver is considered for restart. Fingerprint restart additionally
waits for a successful repair pass. An incomplete pass/pending fingerprint
restart is logged, not silently declared successful.

The Wi-Fi enable watchdog is deliberately removed: a disabled state does not
prove failure and must not override intentional Wi-Fi off. Driver/HAL recovery
remains. This is not a claim of improved battery life or measured boot timing.

## Host regression results

The harness redirects Android filesystem paths into temporary directories and
mocks privileged commands. Only the host's ordinary kernel FD-lock operation
is real. Never run the original Android script directly on a host.

```bash
# Baseline snapshot, under the same regression harness:
git show ce87273:load_drivers.sh > /tmp/load_drivers-before.sh
LOAD_DRIVERS_SCRIPT=/tmp/load_drivers-before.sh python3 -m unittest discover -s tests -v
# Expected baseline result: 24 failures, 5 passes (29 tests).

python3 -m unittest discover -s tests -v
# Cleanup result: all 29 passed.
sh -n load_drivers.sh
git diff --check
# Both passed.
```

Tests exercise sequential/concurrent repeats and concurrent distinct stages;
early-stage failure followed by late-stage recovery; stage/manual dispatch; bounded
late waits and no early sleeps; partial/failed module and hardware repair retry;
both individual FOD results; module reload invalidation; failed HAL restart
retry; absent HALs; stale lock files and actual lock-holder process death;
unavailable locking; fresh reboot state; intentional Wi-Fi off; hardware repair
arguments, patched/stock selection and unchanged repository tuning values.

These are host-shell mocks, not Android firmware or hardware emulation.

## Authorized phone installation

The connected SM-S906B / `g0s` was verified as the single ADB target, with root
ADB shell and SELinux Enforcing. Its three installed copies (including
`boot-completed.d`) were identical to **1da0644**, not the repository's later
conservative-tuning revision. To avoid an unrelated settings change, the phone
received the cleanup prefix plus its **byte-identical original tuning block**.
No prior conservative-tuning PR was silently installed.

This deployment variant intentionally retains the old tuning block's existing
best-effort/repetition behavior. Repository tuning-coalescing behavior does not
apply to this phone variant. Boot repair/locking/HAL/Wi-Fi logic is identical to
the repository prefix. Do not confuse this variant with an exact deployment of
the entire current repository script.

### Deterministic variant derivation

Run from this cleanup revision; choose an output outside startup directories:

```python
from pathlib import Path
import hashlib
import subprocess

old = subprocess.check_output(["git", "show", "1da0644:load_drivers.sh"]).decode()
assert hashlib.sha256(old.encode()).hexdigest() == (
    "5f75f1374b244b98c9e6ceeb5b0a217e981c6765a5b6a53aeb0153bd24fd4a1c"
)
current = Path("load_drivers.sh").read_text()
prefix = current[:current.index("# Keep existing best-effort tuning values.")]
legacy = old[old.index("# 10. Network & VPN"):old.index('echo "=== load_drivers finished')]
variant = (
    prefix
    + "# Existing phone tuning retained verbatim; not part of this cleanup.\n"
    + legacy
    + 'echo "=== load_drivers finished ($STAGE) ==="\n'
)
assert variant[variant.index("# 10. Network & VPN"):variant.index('echo "=== load_drivers finished')] == legacy
Path("/tmp/load_drivers-device.sh").write_text(variant)
print(hashlib.sha256(variant.encode()).hexdigest())
```

| Item | SHA-256 |
| --- | --- |
| Original phone script / exact backups | `5f75f1374b244b98c9e6ceeb5b0a217e981c6765a5b6a53aeb0153bd24fd4a1c` |
| Retained original tuning block | `9c683e8792b8b5dbfae8a2a55eebc6795f3ae7be0c7d7d46692bff8391b449a8` |
| Shared cleanup prefix | `f51a725f60556316acd953580a56c1a7a9d50be91f4e033885f88f444acd67b3` |
| Installed variant (9,589 bytes) | `f4b93861617351dd99ee4b3c93a39eb062a823469d7d08874a50f65bbdf5eaf1` |

```bash
LOAD_DRIVERS_SCRIPT=/tmp/load_drivers-device.sh LOAD_DRIVERS_LEGACY_TUNING=1 \
  python3 -m unittest discover -s tests -v
# Actual variant: 26 passed; 3 tuning-only checks explicitly skipped.
sh -n /tmp/load_drivers-device.sh
# Passed.
```

The legacy flag excludes only repository-specific tuning value/count/readiness
assertions. All boot-stage, concurrent lock, hardware-repair, HAL retry and
Wi-Fi-off checks still run. Separately, all three phone readbacks were compared
byte-for-byte with the tested variant and their extracted tuning blocks with
the original phone backup.

### Backup, replacement and verification record

- Exact-byte backups on the Mac: `$HOME/g0s-boot-cleanup-c18372dd.wodzx6aw/`.
  This directory also contains the installed variant, install/rollback scripts,
  per-stage readbacks, before/after observations and `verification.json`.
- Exact-byte phone backups: `/data/adb/load_drivers-backups/c18372dd/`.
  The three original stage files retain UID/GID `0:0`, mode `0755`, original
  mtime, and SELinux context `u:object_r:adb_data_file:s0`.
- Original hashes and metadata were rechecked before replacement; unknown
  divergence, an active loader, an ambiguous target or missing validation would
  abort installation. Android `flock -n` FD support and the module/process tools
  were verified available.
- All replacement files were prepared outside startup directories on the same
  filesystem, hash-checked, and syntax-checked with Android `/system/bin/sh` and
  KernelSU BusyBox `ash` in standalone mode. No boot script was executed.
- Each destination was replaced using an atomic rename, with rollback on
  installation/check failure. The three-path update is **not** a single atomic
  transaction; it was performed after boot completion with no loader running.
- Final readbacks: all three hashes and bytes match the tested variant; UID/GID
  `0:0`, mode `0755`, context `u:object_r:adb_data_file:s0` verified.
- Immediate observations: SELinux remained Enforcing; `dhd`, `fingerprint`,
  `fingerprint_sysfs` and `bcm43xx` remained loaded; Wi-Fi and fingerprint HAL
  PIDs were unchanged; Bluetooth HAL remained present; qbt nodes retained
  `system:system` / `0660`; Wi-Fi remained enabled. This is not a new functional
  Wi-Fi, Bluetooth or fingerprint test.

### Recovery

An executable, syntax-checked rollback is retained on the phone:

```bash
# Only if rollback is deliberately needed, with the authorized root ADB shell:
adb shell /system/bin/sh /data/adb/load_drivers-backups/c18372dd/rollback.sh
```

It refuses unknown current script hashes or an active loader, validates all
backups first, stages replacements outside startup directories, then restores
all original bytes, ownership, mode, SELinux label and mtime. Its execution was
**not tested on the live phone** because that would undo the authorized cleanup.
Rollback restores the old Wi-Fi-forcing watchdog too. Keep the host backups if
ADB becomes unavailable; no partition/kernel changes were made.

## Remaining limits

No boot-stage hardware actions, HAL restarts, reboot, flashing, kernel rebuild
or unrelated runtime settings changes were performed. The installed script has
not yet run through a real early/service/completed boot sequence. Next-boot
ordering/timing, Wi-Fi/BT functionality, fingerprint enrollment/unlock and actual
recovery after a hardware failure remain unverified. No zero-risk guarantee or
battery-gain claim follows from source preservation and static installation
checks. Reboot/device functional testing requires separate authorization.
