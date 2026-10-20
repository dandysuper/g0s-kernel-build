# Handoff — g0s (Galaxy S22+) LineageOS 24.0 kernel: Wi-Fi fix via source rebuild

**Last updated:** 2026-10-20 06:52 (Asia/Almaty)
**Read this whole file before touching anything.** §1 is the state (build done, zip ready), §3 is the size arithmetic, **§5 is the root cause**, §6 is the fix that worked, §7 is the repack/flash procedure.

---

## 1. TL;DR / current state

Goal: rebuild the kernel from source so BCM4389 **Wi-Fi + Bluetooth** work on LineageOS 24.0 (Android 17) on `g0s` (SM-S906B, Exynos 2200).

**STATUS: BUILD COMPLETE — FLASHABLE ZIP IS READY. Nothing is blocked.**

```
~/Downloads/ExtremeKRNL-g0s-LineageOS24-rebuild-KSU.zip   45,042,643 bytes
```

- ✅ Kernel builds end to end in CI (run `39306330781`, all steps success).
- ✅ **Size blocker solved.** Root cause was **not** LTO — it was `CONFIG_KASAN=y` (+ `KASAN_HW_TAGS`) and `CONFIG_DEBUG_INFO=y` in the merged config. Disabling both dropped the module set from 113,854,768 → **56,524,808 bytes** (below stock's 58,998,976) and the Image from 53,348,864 → **32,733,696** (stock 28,965,376).
- ✅ **`vendor_dlkm` now fits:** EROFS lz4hc,12 = 18,989,056 bytes, padded to exactly **19,685,376** (stock was 19,079,168 — same ~1 MB headroom).
- ✅ All four images built, verified, and packaged (see §7).

**Next action is on the device, not in this repo:** flash and confirm Wi-Fi + BT come up.

```
out/boot.img          34,541,568
out/vendor_boot.img   17,162,240
out/vendor_dlkm.img   19,685,376  (exact partition size)
```
- [ ] Flash all four images together, boot, check Wi-Fi/BT
- [ ] If Wi-Fi still dead, the remaining suspect is `CONFIG_BCMDHD_PCIE` firmware path, not the config gates (they pass)

---

## 2. Environment, repos, where things live

| Thing | Value |
|---|---|
| Local working dir | `/Users/danb/Documents/kernel` |
| Local git remote | `https://github.com/dandysuper/g0s-kernel-build.git` (branch **`master`**) |
| CI repo | `dandysuper/g0s-kernel-build`, workflow `.github/workflows/build.yml` |
| Kernel source (CI) | `https://github.com/ExtremeXT/android_kernel_samsung_s5e9925`, branch `lineage-24.0` |
| Toolchain (CI) | `LineageOS/android_prebuilts_clang_kernel_linux-x86_clang-r416183b` |
| Local LLVM (host) | `/opt/homebrew/opt/llvm/bin` — **keg-only, not on PATH**, prepend manually |
| Deliverable | flashable zip in `~/Downloads/` |

**CI quirks — read before editing the workflow:**
- **GitHub artifact storage quota is exhausted account-wide.** `upload-artifact` fails with "Artifact storage quota has been hit". The workflow publishes each build as a **Release** instead (`gh release create build-<timestamp>`), which is metered separately. **Do not convert this back to artifacts.**
- Dispatch with `workflow_dispatch --ref master`. The `push:` trigger points at `main` and is unused — always dispatch `master`.
- `gh` is slow here; several calls exceeded an 18 s tool timeout. Redirect to a file and grep (`gh run view <id> --log-failed > ci/x.txt`) rather than streaming.

**Key local paths (already prepared — do not recreate, do not "clean up"):**
```
artifact/Image                 # last Image (53,348,864 B) — the KASAN-bloated one, TOO BIG
artifact/modules.tar.gz        # matching modules (unstripped, 113.8 MB of .ko)
modules_out/lib/modules/5.10.260-g0d060df61c11-dirty/
work/dlkm/lib/modules/         # STOCK module set: 319 .ko, 58,998,976 B — THE SIZE TARGET
work/ek/files/vendor_dlkm.img  # STOCK vendor_dlkm, 19,685,376 B — THE PARTITION CEILING
work/orig_boot_out/kernel      # STOCK Image, 28,965,376 B — THE SIZE TARGET
work/vr1/lib/modules/modules.load  # preserved load order — MUST NOT be regenerated
work/boot/ramdisk              # LineageOS 24.0 Android 17 init ramdisk
work/vboot/                    # dtb, bootconfig, vendor_ramdisk00 (LineageOS)
out/boot.img, out/vendor_boot.img  # built OK; out/vendor_dlkm.img MISSING (repack step 7 failed)
```

Those three "size target" files are the reference measurements the entire diagnosis is anchored to. Deleting them destroys the ability to verify the fix.

---

## 3. The blocker (and its arithmetic)

`vendor_dlkm` = **4806 blocks × 4096 = 19,685,376 bytes**, fixed by the partition table. Not growing.

| | stock ExtremeKRNL | our (KASAN) build | ratio |
|---|---|---|---|
| kernel `Image` | 28,965,376 | 53,348,864 | **1.84×** |
| modules, uncompressed | 58,998,976 | 113,854,768 | **1.93×** |
| `vendor_dlkm.img` @ lz4hc,12 | 19,079,168 | 30,330,880 | **1.59×** |
| `dhd.ko` | 7,671,936 | 14,418,840 | 1.88× |

Compression is already maxed: `lz4hc,12` → 30,330,880; `lz4hc,9` → 30,420,992. `lz4,9` / `lz4,1` are **rejected** by `mkfs.erofs` 1.9.4 here (only `lz4hc` levels 1–12 accepted). There is no compression setting that closes a 1.59× gap.

**The module set is not the problem** — 320 modules vs stock's 319, the only addition being `hid-primax.ko` (10,312 B). Every *common* module is simply 1.4–2.5× larger.

---

## 4. Already solved — do not re-litigate or undo

1. **Boot / init.** `boot.img` = new kernel + LineageOS 24.0's own Android 17 init ramdisk (not the Samsung OneUI A12 one shipped by ExtremeKRNL).
2. **Storage / encryption.** LineageOS `vendor_ramdisk00` preserved, so `fstab.s5e9925` keeps `wrappedkey_v0`, `metadata_encryption`, and `/system_ext`.
3. **Bluetooth.** Android first-stage init loads modules in **strict mode** and aborts the entire list on the first failure. `dhd.ko` must **not** be in the early ramdisk list (it can't reach firmware before `/vendor` mounts) or `bcm43xx.ko` never loads. The preserved `work/vr1/lib/modules/modules.load` encodes the correct order — **never regenerate it**.
4. **Original Wi-Fi root cause.** ExtremeKRNL was built with the **`r11s`** (S23 FE) config: `CONFIG_R11S_WIRELESS=y`, `CONFIG_BCM4389` unset, `CONFIG_BCMDHD_PCIE` unset. `g0s` needs `CONFIG_BCM4389=m` + `CONFIG_BCMDHD_PCIE=y` + `CONFIG_BT_BCM43XX=m`. All three are now hard-gated.
5. **`cmupmucal.ko` link failure.** `undefined! vclk_debug_init` / `cmucal_dbg_set_cmu_top_base`. Fixed with `CONFIG_CMUCAL_DEBUG=n` — the header declares those externs while `cmucal-debug.c` only defines them under the same guard, and the symbol is `tristate default y`. `CONFIG_DEBUG_FS` stays on. **Do not re-enable `CMUCAL_DEBUG`.**

---

## 5. ROOT CAUSE of the size blowup (solved — this is the important section)

The original hypothesis was ThinLTO. **It was wrong, and it is now proven wrong.**

Run `39305293253` dumped the real config. Two results settled it:

```
CONFIG_CC_IS_CLANG=y
CONFIG_LD_IS_LLD=y            <- the kconfig LTO probe DID work
CONFIG_LTO_NONE=y
(no LTO choice in init/Kconfig)   <- this tree has NO LTO support at all
(no LTO_CLANG symbol)
```

So `CONFIG_LTO_CLANG_THIN` is unreachable in this Samsung 5.10 tree, no matter what `scripts/config` sets. **Stop trying to enable LTO.**

The same dump contained the actual culprits:

```
CONFIG_KASAN=y
CONFIG_KASAN_HW_TAGS=y
CONFIG_DEBUG_INFO=y
CONFIG_DEBUG_INFO_DWARF4=y
CONFIG_INIT_ON_ALLOC_DEFAULT_ON=y
CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE=y
```

**`CONFIG_KASAN=y`** is the answer:

- KASAN instruments **every load and store** → ~1.8× code growth. That is our 1.84× on the Image.
- KASAN adds **redzones around every global** → that is the 10× `.data` and the `.rela.data` section that stock does not have at all.
- `CONFIG_DEBUG_INFO=y` piles full DWARF on top, which is why the module tarball was ~114 MB.

The section-level evidence that pointed at this, for reference (ours vs stock `dhd.ko`):

```
                      ours          stock
.rela.text         5,682,912      4,223,400
.text              2,702,192      2,090,144
.rodata            1,682,400        626,516
.rela.data         1,665,720      (ABSENT)
.data              1,252,832        125,720   <-- 10x, redzones
.symtab              594,912        287,736
```

Dead ends already ruled out with evidence — **do not re-test these**:
- **DWARF stripping.** `llvm-strip --strip-debug` freed **0 bytes** on all 320 modules. (`INSTALL_MOD_STRIP=1` also silently does nothing with `STRIP=llvm-strip`; the CI now strips explicitly, which is correct hygiene but was never the fix.)
- **`.BTF`.** Not present in our modules.
- **Extra modules.** Only `hid-primax.ko`, 10 KB.
- **Compression.** Exhausted, see §3.
- **IKCONFIG diff.** Neither Image embeds `/proc/config.gz`; `/tmp/ikcfg.py` correctly reports "no embedded config found". Expected, not a bug.

---

## 6. The fix that worked (run `39306330781`, success)

Result: modules **56,524,808** bytes (stock 58,998,976), Image **32,733,696** (stock 28,965,376). `vendor_dlkm.img` = 18,989,056 compressed, padded to 19,685,376. Confirmed by repack.

In the `Configure` step of `.github/workflows/build.yml`, the dead LTO block was replaced with:

```
scripts/config --file out/.config --disable KASAN
scripts/config --file out/.config --disable KASAN_GENERIC
scripts/config --file out/.config --disable KASAN_HW_TAGS
scripts/config --file out/.config --disable KASAN_SW_TAGS
scripts/config --file out/.config --disable KASAN_INLINE
scripts/config --file out/.config --disable DEBUG_INFO
scripts/config --file out/.config --enable  DEBUG_INFO_NONE
scripts/config --file out/.config --disable INIT_ON_ALLOC_DEFAULT_ON
scripts/config --file out/.config --disable INIT_ON_FREE_DEFAULT_ON
```

plus two fail-fast gates: if `CONFIG_KASAN` or `CONFIG_DEBUG_INFO=y` survives `olddefconfig`, the run dies in ~5 minutes instead of producing another unfittable build.

**First action for the incoming agent:**

```bash
cd /Users/danb/Documents/kernel
DISABLE_AUTO_UPDATE=true gh run view 39306330781 \
  --repo dandysuper/g0s-kernel-build --json status,conclusion,jobs \
  --jq '{s:.status,c:(.conclusion|tostring),steps:[.jobs[0].steps[]|select(.status!="pending")|{n:.name,c:(.conclusion|tostring)}]}'
```

Then judge by the numbers, not by the green tick:

- **`bytes:` in the "Build modules" step ≈ 59,000,000** → fix worked, go to §7.
- **`bytes:` still ~113,000,000** → KASAN re-enabled by something. Re-dump `grep -E "^CONFIG_KASAN" out/.config` and find what selects it (likely a fragment in `arch/arm64/configs/g0s.config`, in which case pass `--disable` **after** `merge_config.sh`, which is already the order — so check for a second merge or an explicit `select` in Kconfig).
- **`bytes:` around 70–80 MB** → KASAN off but something else still heavy; the Image size will tell you which (compare against 28,965,376).

---

## 7. Once the module set fits — repack and flash

```bash
cd /Users/danb/Documents/kernel
TAG=$(DISABLE_AUTO_UPDATE=true gh release list --repo dandysuper/g0s-kernel-build --limit 1 | awk '{print $NF}')
rm -rf artifact modules_out && mkdir -p artifact
DISABLE_AUTO_UPDATE=true gh release download "$TAG" --repo dandysuper/g0s-kernel-build --dir artifact --clobber
mkdir -p modules_out && tar -C modules_out -xzf artifact/modules.tar.gz
PATH="/opt/homebrew/opt/llvm/bin:$PATH" python3 repack.py \
  --image   artifact/Image \
  --modules modules_out/lib/modules/5.10.260-g0d060df61c11-dirty
```

`repack.py` writes `out/boot.img`, `out/vendor_boot.img`, `out/vendor_dlkm.img`, and packages
`~/Downloads/ExtremeKRNL-g0s-LineageOS24-rebuild-KSU.zip` (boot + vendor_boot + dtbo + vendor_dlkm).

**Verification already performed on the shipped zip (`build-20261019-1920` → repack):**

| Check | Result |
|---|---|
| `unzip -t` on the zip | *No errors detected in compressed data* |
| `stat -f%z out/vendor_dlkm.img` | **19,685,376** — exact partition size |
| zip contents | `boot.img`, `vendor_boot.img`, `dtbo.img`, `vendor_dlkm.img`, `update-binary`, `updater-script` |
| kernel vermagic (from `boot.img`) | `5.10.260-g0d060df61c11-dirty SMP preempt mod_unload modversions aarch64` |
| module vermagic (`dhd.ko` in dlkm) | identical string |
| `.ko` count inside `vendor_dlkm` | **320** |
| `modules.dep` path prefix | `/vendor_dlkm/lib/modules/…` (correct, depmod resolvable) |
| `dhd.ko` dependencies | resolve to `pcie-exynos-rc.ko` + `exynos-pcie-iommu.ko` (the PCIe glue it binds to) |
| `dhd.ko` in early `modules.load` | **absent** — correct, avoids the strict-mode abort |
| `bcm43xx` in early `modules.load` | present at line 179, reachable |
| `modules.load` entries | 313 |

Everything checkable without the phone has been checked. **What remains is a physical step: flash and observe.**
- Flash `boot`, `vendor_boot`, `dtbo`, `vendor_dlkm` **together**. A kernel/module mismatch = no Wi-Fi and usually no touch.
- On boot, check: `dmesg | grep -i bcmdhd`, `dmesg | grep -i bcm43xx`, `ls /sys/class/rfkill/`, `ip link` for `wlan0`, `lsmod | grep dhd`.
- If `dhd` never appears, a module ordered before it in `modules.load` failed under strict mode — `dmesg | grep -iE "failed|not found" | head` names it.
- If `dhd` loads but `wlan0` never appears, the failure is firmware/PCIe rather than the module set: confirm the BCM4389 blobs are present under `/vendor/etc/wifi/`, since `dhd.ko` cannot bring the chip up without them.

---

## 7b. Bluetooth + fingerprint patch (standalone, flashes one partition)

Built on request: a zip that fixes **Bluetooth and fingerprint** without touching the kernel, dtbo or vendor_dlkm.

**Both failures are the same bug.** Measured against the real release zip (`ExtremeKRNL-v2.5_g0s_AOSP_KSU_05-10-2026_03-11-33.zip`), whose ramdisk carries a 319-entry `modules.load`:

```
entry  module                  consequence
 127   dhd                     fails here -- /vendor not mounted yet
 180   bcm43xx                 Broadcom BT power/rfkill   NEVER REACHED
 258   sec_debug_coredump      sits between the two fixes
 270   input_booster_lkm       sits between the two fixes
 276   fingerprint             sensor driver              NEVER REACHED
 277   fingerprint_sysfs       its dependency             NEVER REACHED
 282   s2dos05-regulator       PMIC regulator, harmless
```

Android first-stage init loads that list in **strict mode** and aborts the entire remainder on the first failure. `dhd.ko` cannot probe before `/vendor` is mounted — its firmware lives there — so it dies at 127 and entries 128–319 are skipped. That single abort is why Bluetooth is dead *and* why fingerprint enrolment never completes: `bcm43xx` and both fingerprint modules are downstream of it.

### v1 was wrong — it removed `dhd` and broke Wi-Fi

The first patch deleted `dhd` from the fragment outright. Bluetooth came up (that proved the abort theory) but Wi-Fi died completely: `dhd` is the Broadcom Wi-Fi driver, so removing it from the only list that was reaching it meant no `wlan0` at all.

That failure is the diagnostic that resolved the whole picture. `/vendor_dlkm/lib/modules/modules.load` contains **exactly those same five modules**:

```
sec_debug_coredump
fingerprint
fingerprint_sysfs
input_booster_lkm
dhd
```

and all five `.ko` files are present in `/vendor_dlkm`. So the vendor's design is: the dlkm fragment loads the bulk, and these five are loaded from `/vendor_dlkm` later, when `/vendor` exists. They are in the fragment list **as well**, where they cannot possibly succeed — and because loading is strict, their failure aborts the fragment, and init never gets as far as processing `/vendor_dlkm`, so `dhd` never loads from anywhere. That is why stock ExtremeKRNL had neither Wi-Fi nor Bluetooth.

### v2 — drop exactly those five

Removing precisely the set `/vendor_dlkm` owns:

* the fragment completes, so nothing after them is skipped;
* `bcm43xx` (now 179) loads → **Bluetooth**;
* init proceeds to `/vendor_dlkm`, which loads `dhd` and the fingerprint modules with `/vendor` available → **Wi-Fi + fingerprint**.

**Proof — the complete diff against stock:**

```
$ diff stock/modules.load patched/modules.load
127d126
< dhd.ko
258d256
< sec_debug_coredump.ko
270d267
< input_booster_lkm.ko
276,277d272
< fingerprint.ko
< fingerprint_sysfs.ko
```

314 entries. `bcm43xx` 180 → 179. Nothing else moves; `s2dos05-regulator` keeps its place.

**Dependency check:** the only remaining module that referenced any dropped module was `fingerprint.ko → fingerprint_sysfs.ko`, and both are dropped together, so nothing is orphaned. `fingerprint`'s other dependency, `exynos_pm_qos.ko`, stays in the fragment.

The other image inputs are **byte-identical**: `dtb` (369,404 B), `bootconfig` (28 B), `vendor_ramdisk00` (211,300 B — the LineageOS fstab and init binaries). `dhd.ko` was never a file in this ramdisk (it lives in `vendor_dlkm`), so no module file was removed.

**The artifact:** `~/Downloads/g0s-fix-wifi-bt-fingerprint-v2.zip` (12,671,094 bytes)

| Check | Result |
|---|---|
| zip integrity | `No errors detected in compressed data` |
| files in zip | `updater-script`, `update-binary`, `files/vendor_boot.img` — nothing else |
| `package_extract_file` calls | **1**, targeting `/dev/block/by-name/vendor_boot` |
| `modules.load` | 314 entries |
| the five dropped | `dhd`, `fingerprint`, `fingerprint_sysfs`, `input_booster_lkm`, `sec_debug_coredump` |
| `bcm43xx` | line 179, still present |
| `dtb` / `bootconfig` / `vendor_ramdisk00` | byte-identical to stock (`cmp`) |
| built for kernel | `5.10.269-ExtremeKRNL-v2.5+` |

**Flash it:**

```bash
# recovery: reboot to recovery, "Apply update from ADB", then
adb sideload ~/Downloads/g0s-fix-wifi-bt-fingerprint-v2.zip

# or no recovery, rooted device (KernelSU):
./flash_bt_adb.sh --zip ~/Downloads/g0s-fix-wifi-bt-fingerprint-v2.zip --mode dd
```

`flash_bt_adb.sh` handles both paths, pulls the image out of the zip, backs up the current `vendor_boot` partition to `/data/local/tmp/vendor_boot.bak.img`, writes with `dd bs=4096`, and md5-checks the readback.

**Note on the fingerprint symptom.** "Doesn't finish registering" is exactly what a missing sensor driver looks like: the Settings enrolment UI opens, the HAL cannot talk to the sensor, and the flow times out at the last step rather than failing outright. If enrolment still fails after this flash, the driver is up and the remaining suspect is the calibration/NVRAM data under `/vendor/etc/fingerprint/`, which is a ROM-side file problem rather than the module list.

This patch is built for the **`5.10.269-ExtremeKRNL-v2.5+`** kernel, which is what the release zip installs. If the device is ever moved to the newer `5.10.260-g0d060df61c11-dirty` kernel, rebuild against that kernel's `vendor_boot.img` (`out/vendor_boot.img`) — the modules inside `vendor_ramdisk01` must match the running kernel or nothing loads.

**Rebuild the patch from scratch:**

```bash
# 1. take vendor_boot.img from the real release zip (NOT from work/ek -- that
#    copy was overwritten with a rebuilt image and is not ground truth)
mkdir -p work/btorig
unzip -o ~/Downloads/ExtremeKRNL-v2.5_g0s_AOSP_KSU_05-10-2026_03-11-33.zip \
  files/vendor_boot.img -d work/btorig
python3 unpack_bootimg.py --boot_img work/btorig/files/vendor_boot.img --out work/btorig/u

# 2. extract vendor_ramdisk01
cd work/btorig/u && lz4 -l -d -f vendor_ramdisk01 r.cpio \
  && mkdir -p x && cd x && cpio -idm --quiet < ../r.cpio && cd ../../../..

# 3. drop the five /vendor_dlkm owns, repack, wrap
python3 build_bt_patch.py
python3 package_patch_zip.py --image work/btmin_vendor_boot.img \
  --out "~/Downloads/g0s-fix-wifi-bt-fingerprint-v2.zip" --kernel "5.10.269-ExtremeKRNL-v2.5+"
```

`build_bt_patch.py` constants:

| constant | value | effect |
|---|---|---|
| `DROP` | `dhd`, `fingerprint`, `fingerprint_sysfs`, `input_booster_lkm`, `sec_debug_coredump` | exactly the set `/vendor_dlkm/lib/modules/modules.load` declares |
| `REQUIRED` | `bcm43xx` | build fails if it is missing from the source list |
| `MIN_ENTRIES` | `300` | guards against an accidental mass deletion |

The script cross-checks `DROP` against `work/dlkm/lib/modules/modules.load` and **refuses to build** if it would drop a module `/vendor_dlkm` does not promise to load — that is the check that prevents ever producing another Wi-Fi-killing zip.

It rebuilds `vendor_boot.img` reusing the source `dtb` / `bootconfig` / `vendor_ramdisk00` untouched. Verify the rebuild with:

```bash
diff work/btorig/u/x/lib/modules/modules.load work/btv2/u/x/lib/modules/modules.load
# expected: 127d126, 258d256, 270d267, 276,277d272
```

**If Bluetooth is still dead after flashing**, ordering was not the cause on your build — check, in this order:
```bash
adb shell 'dmesg | grep -iE "bcm43xx|bluetooth|rfkill"' | head -30
adb shell 'ls -l /sys/class/rfkill/'
adb shell 'lsmod | grep -E "bcm43xx|bluetooth"'
adb shell 'dmesg | grep -iE "failed|not found" | head'
```
`bcm43xx` loaded but no rfkill device → the module is up and the failure is firmware/NVRAM under `/vendor/etc/bluetooth/` or `/vendor/firmware/`, which is a ROM-side file problem, not this patch.

---

## 8. Current state of the code

### `.github/workflows/build.yml` — 10 steps, `master`, `timeout-minutes: 350`
`Checkout → deps → clang → KernelSU Next → Configure → Image → modules → verify → package → publish release`

Hard gates, all fail-fast:
- **size:** `KASAN` must be off, `DEBUG_INFO=y` must not survive `olddefconfig`
- **config:** `BCM4389=m`, `BCMDHD_PCIE=y`, `BT_BCM43XX=m`, `KSU=y`, `DEBUG_FS=y`, `CMUCAL_DEBUG` off, `R11S_WIRELESS` off
- **modules:** after explicit `llvm-strip --strip-debug`, total `.ko` bytes must be **≤ 70,000,000**
- **verify:** vermagic **prefix** match (not equality — the Image carries the bare release, the module carries release + flags), and `dhd.ko`/`cfg80211.ko`/`bcm43xx.ko` must exist

### `repack.py` (host)
- flattens modules; strips via `llvm-strip`/`aarch64-linux-gnu-strip` if found (on this Mac that freed 0 bytes — there was no DWARF)
- rewrites `modules.dep` per partition (`/lib/modules/` for the ramdisk fragment, `/vendor_dlkm/lib/modules/` for the dlkm image)
- EROFS ladder `lz4hc,12 → lz4hc,9 → lz4,9 → lz4,1`, each allowed to fail and move on, then **hard-fails** with a clear message
- copies the preserved `modules.load` (the load order that makes BT work)
- pads `vendor_dlkm.img` to exactly 19,685,376 bytes

### Commits on `master` (newest first)
```
fbc9cbb     docs: handoff with root cause (KASAN + DEBUG_INFO, not LTO) and current build state
be395ab     fix size root cause: disable KASAN and DEBUG_INFO (1.84x bloat); tree has no LTO support at all
224fc1b     diagnose: dump size-relevant flags and LTO Kconfig presence when ThinLTO is unavailable
ab029e5     pass toolchain vars to olddefconfig so CC_IS_CLANG/LLD_VERSION probe works and ThinLTO can be selected
5fb8ea1     enable ThinLTO: LTO_NONE inflated Image and every module by 1.84x, modules cannot fit vendor_dlkm
c41137b     strip modules explicitly with llvm-strip; fix erofs ladder (lz4hc max level 12)
e59b85e     fix: vermagic gate compares prefix not equality (module = release + flags)
```

### Helper scripts in `/tmp` (may be gone on a new machine)
- `/tmp/secs.py` — per-section size table for a `.ko` (uses `/opt/homebrew/opt/llvm/bin/llvm-objdump`)
- `/tmp/ikdiff.py` — module-set diff, ours vs stock (`work/flat_modules` vs `work/dlkm/lib/modules`)
- `/tmp/ikcfg.py` — IKCONFIG extraction attempt (both images lack it; reports nothing, as expected)

---

## 9. Gotchas that already cost time

1. **`CONFIG_LTO_NONE` is not a bug in this tree** — the tree simply has no LTO choice in `init/Kconfig`. LTO is a dead end here; don't spend another cycle on it.
2. **`CONFIG_LD_IS_LLD=y` proves the toolchain probe works.** If you see it missing, that's a `CC`/`LD` export problem — but it is *set*, so that path is fine.
3. **`scripts/config` succeeding ≠ the flag took.** Always gate on the **resulting `out/.config`** after `olddefconfig`. That one habit saved two 90-minute builds.
4. **`INSTALL_MOD_STRIP=1` does not strip** with `STRIP=llvm-strip` — it passes the value through as an argument. Strip explicitly.
5. **`llvm-strip --strip-debug` freed 0 bytes.** No DWARF existed; the bloat was real code/data. Don't chase stripping.
6. **`lz4hc,14` / `lz4hc,16` are invalid** for `mkfs.erofs` 1.9.4 and abort a naive ladder. Max is `lz4hc,12`.
7. **Homebrew LLVM is keg-only** — `PATH="/opt/homebrew/opt/llvm/bin:$PATH"`. `llvm-strip` there is a symlink to `llvm-objcopy`, version 23.1.3.
8. **The Instabox VM cannot build this.** Currently *suspended* (`machine m_agqukqdq433k6wux43iajuftzi is suspended; start it in the instabox dashboard`), and even running it had 4 cores / 7 GB / **11 GB free** while `out/` alone needs 9–12 GB. Needs 30 GB+ before it's worth starting.
9. **GitHub artifact quota is exhausted** — releases only.
10. **`gh` is slow** — redirect logs to files, grep, don't re-run.
11. **Never regenerate `modules.load`** — its order is the fix for the strict-mode Bluetooth failure.
12. **Never delete** `work/dlkm/lib/modules`, `work/ek/files/vendor_dlkm.img`, `work/orig_boot_out/kernel` — they are the reference measurements.

---

## 10. One-paragraph summary for a fresh agent

**The work is done — a flashable zip is sitting in `~/Downloads/ExtremeKRNL-g0s-LineageOS24-rebuild-KSU.zip` (45,042,643 bytes) and the only thing left is flashing it on the device.**

The size blocker is solved. The module set was 113.8 MB against a fixed 19.6 MB `vendor_dlkm` partition; the cause was **`CONFIG_KASAN=y` (+ `KASAN_HW_TAGS`) and `CONFIG_DEBUG_INFO=y`** in the merged config — KASAN inflates code ~1.8× and redzones every global (the 10× `.data`), and DEBUG_INFO adds full DWARF. LTO was a red herring: this Samsung 5.10 tree has no LTO Kconfig at all. Disabling those dropped modules to **56,524,808 bytes** (below stock's 58,998,976) and the Image to **32,733,696** (stock 28,965,376), so `vendor_dlkm.img` compresses to 18,989,056 and pads to exactly 19,685,376. Build `39306330781` is green and `repack.py` confirmed all four images.

**If you are picking this up: flash `boot` + `vendor_boot` + `dtbo` + `vendor_dlkm` together and report whether Wi-Fi and Bluetooth come up.** Everything about boot, encryption, Bluetooth module ordering, the `r11s`→`g0s` Wi-Fi config fix, and the `CMUCAL_DEBUG` link fix is already done and must not be undone. If Wi-Fi is still dead after flashing, the config gates all pass, so the next suspect is the BCMDHD firmware path under `/vendor` rather than anything in this build system.
