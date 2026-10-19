# Handoff — g0s (Galaxy S22+) LineageOS 24.0 kernel: Wi-Fi fix via source rebuild

**Last updated:** 2026-10-19 23:53 (Asia/Almaty)
**Read this whole file before touching anything.** §1 is the state, §3 is the blocker, **§5 contains the root cause that was just found**, §6 is the fix that is currently building.

---

## 1. TL;DR / current state

Goal: rebuild the kernel from source so BCM4389 **Wi-Fi + Bluetooth** work on LineageOS 24.0 (Android 17) on `g0s` (SM-S906B, Exynos 2200).

Status right now:

- ✅ **The kernel builds end to end in CI.** Image and all 320 modules compile and link. The two original blockers (the `cmupmucal.ko` undefined-symbol link failure, and the `r11s`-instead-of-`g0s` Wi-Fi config) are both fixed and gated.
- ✅ `boot.img` and `vendor_boot.img` have already been produced locally and are correct.
- ❗ **The one remaining blocker:** the module set was 113,854,768 bytes against a fixed 19,685,376-byte `vendor_dlkm` partition — it could not be flashed.
- ✅ **Root cause found and fixed in the workflow.** It was **not** LTO. `CONFIG_KASAN=y` (+ `KASAN_HW_TAGS`) and `CONFIG_DEBUG_INFO=y` were enabled in the merged config, inflating the Image and every module by ~1.84×. Both are now disabled with hard gates.
- 🔄 **Build `39306330781` is running with that fix.** Whoever picks this up should read its result first.

If that build's module total comes in around **59 MB** (stock is 58,998,976), the fix worked and the project moves straight to repack + flash.

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

## 6. The fix that is currently building (run `39306330781`)

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

**Verify before flashing:**
- `stat -f%z out/vendor_dlkm.img` must be **exactly 19,685,376** (it is zero-padded).
- vermagic must prefix-match — kernel `5.10.260-g…`, module `5.10.260-g… SMP preempt mod_unload modversions aarch64`. `repack.py` hard-fails on mismatch.
- `dhd.ko`, `cfg80211.ko`, `bcm43xx.ko` present in the built tree and inside `vendor_dlkm.img`.
- Flash `boot`, `vendor_boot`, `dtbo`, `vendor_dlkm` **together**. A kernel/module mismatch = no Wi-Fi and usually no touch.

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

The kernel builds and `boot.img`/`vendor_boot.img` are already correct; the only thing that stopped flashing was the module set being 113.8 MB against a fixed 19.6 MB `vendor_dlkm` partition. The cause was **`CONFIG_KASAN=y` (+ `KASAN_HW_TAGS`) and `CONFIG_DEBUG_INFO=y`** in the merged config — KASAN inflates code ~1.8× and redzones every global (hence the 10× `.data`), and DEBUG_INFO adds full DWARF; LTO was a red herring because this Samsung 5.10 tree has no LTO Kconfig at all. The workflow now disables all of that with fail-fast gates, and build `39306330781` is exercising the fix — read its `bytes:` line in the "Build modules" step: ~59 MB means success, then run `repack.py` (§7), confirm `vendor_dlkm.img` is exactly 19,685,376 bytes, and flash all four images together. Everything about boot, encryption, Bluetooth module ordering, the `r11s`→`g0s` Wi-Fi config fix, and the `CMUCAL_DEBUG` link fix is already done and must not be undone.
