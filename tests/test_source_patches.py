"""Structural checks for the kernel source patches the build workflow applies."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCHES_DIR = ROOT / "patches"
FINGERPRINT = PATCHES_DIR / "0001-fingerprint-qbt2000-idempotent-irq-toggles.patch"
BTS = PATCHES_DIR / "0002-bts-fix-bw-voting-race-and-use-rt-mutex.patch"
CPUPM = PATCHES_DIR / "0003-cpupm-use-atomic-notifier-for-cpupm-events.patch"
UID_STATS = PATCHES_DIR / "0004-uid-sys-stats-convert-uid-lock-to-rt-mutex.patch"
SCHED = PATCHES_DIR / "0005-sched-backport-idle-and-fair-pelt-misfit-fixes.patch"


class SourcePatchTests(unittest.TestCase):
    def test_patch_series_order_and_headers(self):
        patches = sorted(p.name for p in PATCHES_DIR.glob("*.patch"))
        self.assertEqual(patches, [
            "0001-fingerprint-qbt2000-idempotent-irq-toggles.patch",
            "0002-bts-fix-bw-voting-race-and-use-rt-mutex.patch",
            "0003-cpupm-use-atomic-notifier-for-cpupm-events.patch",
            "0004-uid-sys-stats-convert-uid-lock-to-rt-mutex.patch",
            "0005-sched-backport-idle-and-fair-pelt-misfit-fixes.patch",
        ])

    def test_fingerprint_patch_only_drops_the_four_already_state_errors(self):
        text = FINGERPRINT.read_text()
        self.assertEqual(re.findall(r"^diff --git a/(\S+) b/\S+$", text, re.M),
                         ["drivers/fingerprint/qbt2000_common.c"])
        diff = text[text.index("diff --git"):]
        lines = diff.splitlines()
        self.assertEqual([l for l in lines if l.startswith("+") and not l.startswith("+++")], [])
        self.assertEqual([l for l in lines if l.startswith("-") and not l.startswith("---")],
                         ["-\t\t\trc = -EINVAL;"] * 4)
        functions = re.findall(r"^@@ -\d+,\d+ \+\d+,\d+ @@ static int (qbt2000_\w+)\(", diff, re.M)
        self.assertEqual(functions, ["qbt2000_enable_ipc", "qbt2000_disable_ipc",
                                     "qbt2000_enable_wuhb", "qbt2000_disable_wuhb"])
        # Only the return value changes: each guard keeps its log line.
        for state in ("enabled ipc", "disabled ipc", "enabled wuhb", "disabled wuhb"):
            self.assertIn(f'-\t\t\trc = -EINVAL;\n \t\t\tpr_err("already {state}\\n");', diff)

    def test_bts_patch_fixes_bw_lock_race_and_converts_to_rt_mutex(self):
        text = BTS.read_text()
        self.assertEqual(re.findall(r"^diff --git a/(\S+) b/\S+$", text, re.M),
                         ["drivers/bts/bts.h", "drivers/bts/exynos-bts.c"])
        self.assertIn("+#include <linux/rtmutex.h>", text)
        self.assertIn("-\tstruct mutex\t\tmutex_lock;\n+\tstruct rt_mutex\t\tmutex_lock;", text)
        self.assertIn("-\tspin_lock(&btsdev->lock);\n+\trt_mutex_lock(&btsdev->mutex_lock);", text)
        self.assertIn(" \tbts_calc_bw();\n+\trt_mutex_unlock(&btsdev->mutex_lock);", text)
        self.assertIn("-\t\tmutex_init(&btsdev->mutex_lock);\n+\t\trt_mutex_init(&btsdev->mutex_lock);", text)

    def test_cpupm_patch_replaces_rwlock_raw_notifier_with_atomic_notifier(self):
        text = CPUPM.read_text()
        self.assertEqual(re.findall(r"^diff --git a/(\S+) b/\S+$", text, re.M),
                         ["drivers/soc/samsung/exynos-cpupm.c"])
        self.assertIn("-static DEFINE_RWLOCK(notifier_lock);\n-static RAW_NOTIFIER_HEAD(notifier_chain);\n+static ATOMIC_NOTIFIER_HEAD(notifier_chain);", text)
        self.assertIn("+\treturn atomic_notifier_chain_register(&notifier_chain, nb);", text)
        self.assertIn("+\tint ret = atomic_notifier_call_chain(&notifier_chain, event, &v);", text)

    def test_uid_sys_stats_patch_converts_spinlock_buckets_to_rt_mutex(self):
        text = UID_STATS.read_text()
        self.assertEqual(re.findall(r"^diff --git a/(\S+) b/\S+$", text, re.M),
                         ["drivers/misc/uid_sys_stats.c"])
        self.assertIn("+#include <linux/rtmutex.h>", text)
        self.assertIn("-spinlock_t uid_lock[UID_HASH_NUMS];\n+static struct rt_mutex uid_lock[UID_HASH_NUMS];", text)
        self.assertIn("-\t\tspin_lock_init(&uid_lock[i]);\n+\t\trt_mutex_init(&uid_lock[i]);", text)
        self.assertIn("-\treturn spin_trylock(&uid_lock[uid_to_bkt(uid)]);\n+\treturn rt_mutex_trylock(&uid_lock[uid_to_bkt(uid)]);", text)

    def test_sched_patch_backports_idle_and_fair_fixes(self):
        text = SCHED.read_text()
        self.assertEqual(re.findall(r"^diff --git a/(\S+) b/\S+$", text, re.M),
                         ["kernel/sched/fair.c", "kernel/sched/idle.c"])
        self.assertIn("-\t\t\tsa->util_avg  = cfs_rq->avg.util_avg * se->load.weight;\n+\t\t\tsa->util_avg  = cfs_rq->avg.util_avg * se_weight(se);", text)
        self.assertIn("+\t\tif (idle != CPU_NEWLY_IDLE &&\n+\t\t    env.migration_type != migrate_misfit)\n \t\t\tsd->nr_balance_failed++;", text)
        self.assertIn("+\tif (env.idle == CPU_NEWLY_IDLE ||\n+\t    env.migration_type == migrate_misfit)\n \t\tgoto out;", text)
        self.assertIn(" \twhile (!need_resched()) {\n-\t\trmb();\n-\n \t\tlocal_irq_disable();", text)

    def test_workflow_applies_patches_and_enforces_bbr_gates(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        step = workflow.index("name: Apply g0s source patches")
        self.assertLess(step, workflow.index("name: Build kernel image and modules"))
        self.assertIn("git apply --check", workflow[step:])
        self.assertIn("'patches/**'", workflow)
        self.assertIn("--enable TCP_CONG_BBR", workflow)
        self.assertIn("--set-str DEFAULT_TCP_CONG bbr", workflow)
        self.assertIn('CONFIG_DEFAULT_TCP_CONG="bbr"', workflow)


if __name__ == "__main__":
    unittest.main()
