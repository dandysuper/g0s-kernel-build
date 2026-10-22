"""Structural checks for the kernel source patches the build workflow applies."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = ROOT / "patches" / "0001-fingerprint-qbt2000-idempotent-irq-toggles.patch"


class SourcePatchTests(unittest.TestCase):
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

    def test_workflow_applies_patches_fail_closed_before_compiling(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        step = workflow.index("name: Apply g0s source patches")
        self.assertLess(step, workflow.index("name: Build kernel image and modules"))
        self.assertIn("git apply --check", workflow[step:])
        self.assertIn("'patches/**'", workflow)


if __name__ == "__main__":
    unittest.main()
