"""Host-safe regression tests: Android paths and privileged commands are sandboxed.

Run with ``python3 -m unittest discover -s tests -v``.  LOAD_DRIVERS_SCRIPT can
select an old script for the same before/after regression check. No root needed.
LOAD_DRIVERS_LEGACY_TUNING=1 explicitly excludes tuning-only expectations when
checking a deployment variant that intentionally retains its original tuning.
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SOURCE = Path(os.environ.get("LOAD_DRIVERS_SCRIPT", Path(__file__).resolve().parents[1] / "load_drivers.sh"))
LEGACY_TUNING = os.environ.get("LOAD_DRIVERS_LEGACY_TUNING") == "1"

# This dispatcher never executes a privileged Android command. flock is the sole
# exception: its real kernel advisory lock is needed to test concurrent shells.
MOCK = r'''import json, os, pathlib, sys, time
root = pathlib.Path(os.environ["ANDROID_TEST_ROOT"])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
fd = os.open(root / "calls.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.write(fd, (json.dumps([name] + args) + "\n").encode())
os.close(fd)
config = json.loads((root / "config.json").read_text())
for failure in config.get("fail", []):
    if failure in " ".join([name] + args):
        sys.exit(1)
if name == "flock":
    os.execv("/usr/bin/flock", ["flock"] + args)
modules = root / "modules"
if name == "cat":
    for arg in args:
        file = pathlib.Path(arg)
        if not file.is_relative_to(root) or not file.is_file(): sys.exit(1)
        if file.name == "cmd_result" and config.get("fod_first_fails"):
            command = file.with_name("cmd").read_text().strip()
            print(command + (":FAIL" if command == "fod_enable,1" else ":OK"))
        else:
            print(file.read_text(), end="")
elif name == "lsmod":
    print("".join(m + " 1 0\n" for m in modules.read_text().splitlines()), end="")
elif name in ("modprobe", "insmod"):
    if config.get("load_delay"):
        time.sleep(config["load_delay"])
    module = pathlib.Path(args[-1]).stem
    loaded = modules.read_text().splitlines()
    if module not in loaded:
        with modules.open("a") as f: f.write(module + "\n")
    state = root / "sys/module" / module / "initstate"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("live\n")
elif name == "pgrep":
    if not config.get("hal_running", False): sys.exit(1)
    print(1234)
elif name == "getprop":
    print(config.get("boot_completed", "1"))
elif name == "cmd":
    if args == ["wifi", "status"]: print("Wifi is disabled")
elif name in ("iptables", "ip6tables"):
    rule = root / (name + "-rule")
    if "-C" in args: sys.exit(0 if rule.exists() else 1)
    if "-A" in args: rule.touch()
elif name in ("chown", "chmod", "chcon", "sysctl", "pkill", "sleep", "ksud", "resetprop"):
    pass
else:
    print("Unexpected Android command: " + name, file=sys.stderr)
    sys.exit(127)
'''


class AndroidSandbox:
    def __init__(self, source):
        self.temp = tempfile.TemporaryDirectory(prefix="load-drivers-test-")
        self.root = Path(self.temp.name)
        self.source = source
        self.bin = self.root / "system/bin"
        self.bin.mkdir(parents=True)
        self.path("/data/adb").mkdir(parents=True)
        self.path("/proc/sys/kernel/random").mkdir(parents=True)
        self.path("/proc/sys/kernel/random/boot_id").write_text("boot-one\n")
        self.path("/proc/sys/kernel/sched_schedstats").touch()
        self.path("/proc/sys/net/ipv4").mkdir(parents=True)
        self.path("/proc/sys/net/ipv4/tcp_mtu_probing").touch()
        self.path("/proc/sys/net/ipv6").mkdir(parents=True)
        self.path("/sys/class/sec/tsp").mkdir(parents=True)
        self.path("/sys/class/sec/tsp/cmd").touch()
        self.path("/sys/class/sec/tsp/cmd_result").write_text("OK\n")
        self.path("/sys/class/fingerprint/fingerprint").mkdir(parents=True)
        self.path("/sys/class/fingerprint/fingerprint/test").touch()
        self.path("/sys/block/zram0/queue").mkdir(parents=True)
        self.path("/sys/block/zram0/queue/read_ahead_kb").write_text("128\n")
        self.path("/dev").mkdir()
        for name in ("qbt2000_fd", "qbt2000_ipc"):
            self.path("/dev/" + name).touch()
        (self.root / "modules").write_text("")
        self.configure()
        dispatcher = self.root / "mock-command"
        dispatcher.write_text("#!" + sys.executable + "\n" + MOCK)
        dispatcher.chmod(0o755)
        commands = set(re.findall(r"/system/bin/([A-Za-z0-9_-]+)", source))
        commands.update("cat lsmod getprop cmd iptables ip6tables pgrep pkill modprobe insmod chown chmod chcon sysctl sleep resetprop flock".split())
        for name in commands - {"sh"}:
            (self.bin / name).symlink_to(dispatcher)
        self.path("/data/adb/ksud").symlink_to(dispatcher)
        # Rewrite every Android absolute path, not merely commands. Redirections,
        # globs, module names and the kernel boot ID all stay inside the tempdir.
        prefixes = ("/proc", "/system/bin", "/vendor_dlkm", "/data", "/dev", "/sys")
        pattern = r"(?<![A-Za-z0-9_./-])(?:" + "|".join(re.escape(prefix) for prefix in prefixes) + r")(?=/|\b)"
        self.transformed = re.sub(pattern, lambda match: str(self.path(match.group())), source)
        self.transformed = self.transformed.replace("#!" + str(self.bin / "sh"), "#!/bin/sh", 1)
        # BusyBox may prefer built-ins/applets over PATH commands. Functions
        # keep mocked calls sandboxed and observable without changing the loader.
        mock_functions = "\n".join(
            f'{name}() {{ {shlex.quote(str(self.bin / name))} "$@"; }}'
            for name in sorted(commands - {"sh"}))
        self.transformed = self.transformed.replace(
            "#!/bin/sh\n", f"#!/bin/sh\n{mock_functions}\n", 1)
        # Refuse an unhandled absolute Android path before executing any shell.
        checked = self.transformed
        for prefix in ("/proc", "/system/bin", "/vendor_dlkm", "/data", "/dev", "/sys"):
            checked = checked.replace(str(self.path(prefix)), "SANDBOX")
        if re.search(r"(?<![A-Za-z0-9_-])/(?:system|vendor|vendor_dlkm|data|dev|sys|proc)(?:/|\b)", checked):
            raise AssertionError("Unmapped Android absolute path")
        self.env = dict(os.environ, ANDROID_TEST_ROOT=str(self.root), PATH=str(self.bin) + ":/usr/bin:/bin")

    def path(self, android):
        return self.root / android.lstrip("/")

    def set_modules(self, names):
        (self.root / "modules").write_text(names)
        directory = self.path("/sys/module")
        directory.mkdir(parents=True, exist_ok=True)
        for module in directory.iterdir():
            (module / "initstate").unlink()
            module.rmdir()
        for name in names.splitlines():
            module = directory / name
            module.mkdir()
            (module / "initstate").write_text("live\n")

    def configure(self, **kwargs):
        self.config = kwargs
        (self.root / "config.json").write_text(json.dumps(kwargs))

    def script(self, stage):
        directory = self.path("/data/adb/" + stage)
        directory.mkdir(parents=True, exist_ok=True)
        script = directory / "load_drivers.sh"
        # Rewriting can truncate a file that an earlier shell is still parsing.
        if not script.exists():
            script.write_text(self.transformed)
        return script

    def start(self, stage="service.d", arg=None):
        command = shlex.split(os.environ.get("LOAD_DRIVERS_TEST_SHELL", "/bin/sh")) + [str(self.script(stage))]
        if arg is not None:
            command.append(arg)
        return subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def finish(self, process):
        try:
            out, err = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise AssertionError("Sandboxed script exceeded bounded runtime")
        if process.returncode:
            raise AssertionError(f"Script exited {process.returncode}: {out}{err}\n{self.log()}")

    def run(self, stage="service.d", arg=None):
        self.finish(self.start(stage, arg))

    def calls(self, name=None, contains=None):
        file = self.root / "calls.jsonl"
        calls = [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []
        return [c for c in calls if (name is None or c[0] == name) and (contains is None or contains in " ".join(c))]

    def log(self):
        file = self.path("/data/adb/load_drivers.log")
        return file.read_text() if file.exists() else ""

    def repairs(self):
        return self.calls("chown", "qbt2000")

    def close(self):
        self.temp.cleanup()


class LoadDriversTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()

    def setUp(self):
        self.android = AndroidSandbox(self.source)
        self.addCleanup(self.android.close)

    def test_lock_fd_is_explicitly_inherited_by_android_flock(self):
        # Android mksh closes exec-created FDs in external commands unless they
        # are explicitly redirected. Host /bin/sh does not expose this bug.
        calls = re.findall(r"/system/bin/flock -n 9[^;\n]*", self.source)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertRegex(call, r"-n 9\s+9>&9(?:\s|$)")

    def test_sequential_repeats_deduplicate_repairs_and_tuning(self):
        a = self.android
        a.run()
        first_tuning = a.calls("sysctl")
        a.run()
        a.run()
        self.assertEqual(len(a.repairs()), 1)
        if not LEGACY_TUNING:
            self.assertEqual(a.calls("sysctl"), first_tuning)
        self.assertEqual(len(a.calls("modprobe", "dhd")), 1)
        self.assertEqual(a.calls("lsmod"), [])

    def test_repeated_starts_reuse_published_script_without_rewriting(self):
        a = self.android
        script = a.script("post-fs-data.d")
        with mock.patch.object(Path, "write_text") as write:
            self.assertEqual(a.script("post-fs-data.d"), script)
            write.assert_not_called()

    def test_concurrent_repeats_are_serialized(self):
        a = self.android
        a.configure(load_delay=0.08)
        processes = [a.start("post-fs-data.d") for _ in range(4)]
        for process in processes:
            a.finish(process)
        self.assertEqual(len(a.calls("modprobe", "dhd")), 1)
        self.assertEqual(len(a.repairs()), 1)
        if not LEGACY_TUNING:
            self.assertEqual(a.calls("sysctl"), [])
            self.assertEqual(a.calls("iptables"), [])
            self.assertEqual(a.calls("ip6tables"), [])
        self.assertTrue(a.calls("flock", "-n 9"))

    def test_concurrent_different_stages_eventually_repair_once_each(self):
        a = self.android
        a.configure(load_delay=0.08)
        stages = ("post-fs-data.d", "service.d", "boot-completed.d")
        processes = [a.start(stage) for stage in stages]
        for process in processes:
            # Contending late stages may exhaust their bounded lock wait. They
            # must remain safely retryable, just like a skipped early stage.
            out, err = process.communicate(timeout=15)
            self.assertIn(process.returncode, (0, 1), out + err + a.log())
            if process.returncode:
                self.assertIn("Loader busy or flock unavailable; retry", a.log())
        for stage in stages:
            a.run(stage)
            a.run(stage)
        self.assertEqual(len(a.repairs()), 3)
        for module in ("dhd", "fingerprint_sysfs", "fingerprint"):
            self.assertEqual(sum(call[-1] == module for call in a.calls("modprobe")), 1, module)
        state = a.path("/data/adb/load_drivers-state/boot-one")
        for stage in ("early", "service", "completed"):
            self.assertTrue((state / (stage + ".repairs")).is_file(), stage)
        self.assertTrue(a.calls("flock", "-n 9"))

    def test_early_failure_recovers_at_later_boot_stages(self):
        a = self.android
        patched = a.path("/data/adb/modules_fixed/fingerprint.ko")
        patched.parent.mkdir(parents=True)
        patched.touch()
        a.configure(hal_running=True, fail=["insmod"])
        for name in ("qbt2000_fd", "qbt2000_ipc"):
            a.path("/dev/" + name).unlink()
        a.run("post-fs-data.d")
        self.assertNotIn("fingerprint", (a.root / "modules").read_text().splitlines())
        self.assertEqual(a.repairs(), [])
        self.assertEqual(a.calls("sleep"), [])
        self.assertEqual(a.calls("pkill", "android.hardware.biometrics.fingerprint"), [])
        a.configure(hal_running=True)
        for name in ("qbt2000_fd", "qbt2000_ipc"):
            a.path("/dev/" + name).touch()
        for stage in ("service.d", "boot-completed.d"):
            a.run(stage)
            a.run(stage)
        self.assertEqual(len(a.repairs()), 2)
        self.assertIn("fingerprint", (a.root / "modules").read_text().splitlines())
        self.assertEqual(len(a.calls("insmod", "fingerprint.ko")), 2)
        self.assertEqual(len(a.calls("modprobe", "fingerprint_sysfs")), 1)
        self.assertEqual(len(a.calls("modprobe", "dhd")), 1)
        self.assertEqual(len(a.calls("pkill", "android.hardware.biometrics.fingerprint")), 1)

    def test_each_boot_stage_keeps_its_repairs(self):
        a = self.android
        for stage in ("post-fs-data.d", "service.d", "boot-completed.d"):
            a.run(stage)
            a.run(stage)
        self.assertEqual(len(a.repairs()), 3)
        self.assertEqual(len(a.calls("modprobe", "dhd")), 1)
        if not LEGACY_TUNING:
            self.assertEqual(len(a.calls("sysctl", "net.ipv4.tcp_mtu_probing=1")), 1)

    def test_explicit_stages_override_location_and_manual_defaults_to_service(self):
        a = self.android
        a.run("manual", "early")
        a.run("manual", "service")
        a.run("manual", "completed")
        a.run("manual")
        self.assertEqual(len(a.repairs()), 3)

    def test_failed_module_load_is_retried_in_same_stage(self):
        a = self.android
        a.configure(fail=["modprobe -d " + str(a.path("/vendor_dlkm/lib/modules")) + " dhd"])
        a.run()
        self.assertNotIn("dhd", (a.root / "modules").read_text().splitlines())
        a.configure()
        a.run()
        self.assertIn("dhd", (a.root / "modules").read_text().splitlines())
        self.assertEqual(len(a.calls("modprobe", "dhd")), 2)

    def test_module_presence_is_checked_after_stage_completed(self):
        a = self.android
        a.run()
        a.set_modules("fingerprint_sysfs\nfingerprint\n")
        a.run()
        self.assertEqual(len(a.calls("modprobe", "dhd")), 2)

    def test_failed_repair_is_retried_not_marked_complete(self):
        a = self.android
        a.configure(fail=["chown system:system " + str(a.path("/dev/qbt2000"))])
        a.run()
        a.configure()
        a.run()
        a.run()
        self.assertEqual(len(a.repairs()), 2)

    def test_missing_nodes_are_repaired_on_later_invocation(self):
        a = self.android
        for name in ("qbt2000_fd", "qbt2000_ipc"):
            a.path("/dev/" + name).unlink()
        a.run("post-fs-data.d")
        a.path("/dev/qbt2000_fd").touch()
        a.path("/dev/qbt2000_ipc").touch()
        a.run("post-fs-data.d")
        a.run("post-fs-data.d")
        self.assertEqual(len(a.repairs()), 1)

    def test_one_missing_qbt_node_remains_retryable(self):
        a = self.android
        a.path("/dev/qbt2000_ipc").unlink()
        a.run()
        a.path("/dev/qbt2000_ipc").touch()
        a.run()
        a.run()
        self.assertEqual(len(a.repairs()), 2)

    def test_fod_failure_is_retried_before_hal_restart(self):
        a = self.android
        a.configure(hal_running=True)
        a.path("/sys/class/sec/tsp/cmd_result").write_text("FAIL\n")
        a.run()
        self.assertEqual(a.calls("pkill", "android.hardware.biometrics.fingerprint"), [])
        a.path("/sys/class/sec/tsp/cmd_result").write_text("OK\n")
        a.run()
        a.run()
        self.assertEqual(len(a.repairs()), 2)
        self.assertEqual(len(a.calls("pkill", "android.hardware.biometrics.fingerprint")), 1)

    def test_fingerprint_module_reload_reapplies_repairs(self):
        a = self.android
        a.run()
        a.set_modules("dhd\nfingerprint_sysfs\n")
        a.run()
        a.run()
        self.assertEqual(len(a.repairs()), 2)

    @unittest.skipIf(LEGACY_TUNING, "deployment intentionally retains legacy tuning")
    def test_failed_tuning_is_retried_then_coalesced(self):
        a = self.android
        a.configure(fail=["sysctl -w net.ipv4.tcp_mtu_probing=1"])
        a.run()
        a.configure()
        a.run()
        a.run()
        self.assertEqual(len(a.calls("sysctl", "net.ipv4.tcp_mtu_probing=1")), 2)

    @unittest.skipIf(LEGACY_TUNING, "deployment intentionally retains legacy tuning")
    def test_unavailable_zram_is_retried_at_later_stage(self):
        a = self.android
        queue = a.path("/sys/block/zram0/queue/read_ahead_kb")
        queue.unlink()
        queue.parent.rmdir()
        queue.parent.parent.rmdir()
        a.run()
        queue.parent.mkdir(parents=True)
        queue.write_text("128\n")
        a.run()
        a.run("boot-completed.d")
        self.assertEqual(len(a.calls("sysctl", "net.ipv4.tcp_mtu_probing=1")), 1)
        self.assertEqual(queue.read_text().strip(), "0")

    def test_reboot_uses_fresh_state(self):
        a = self.android
        a.run()
        a.path("/proc/sys/kernel/random/boot_id").write_text("boot-two\n")
        a.run()
        self.assertEqual(len(a.repairs()), 2)
        if not LEGACY_TUNING:
            self.assertEqual(len(a.calls("sysctl", "net.ipv4.tcp_mtu_probing=1")), 2)
        self.assertTrue(a.path("/data/adb/load_drivers-state/boot-one").is_dir())
        self.assertTrue(a.path("/data/adb/load_drivers-state/boot-two").is_dir())

    def test_stale_lock_file_does_not_block_and_kernel_lock_releases(self):
        a = self.android
        # A lock file's existence is not lock ownership. Pre-existing files must
        # not become permanent mkdir-style guards, including across a reboot.
        state = a.path("/data/adb/load_drivers-state/boot-one")
        state.mkdir(parents=True)
        for name in ("lock", "lockfile", ".lock"):
            (state / name).touch()
        a.run("post-fs-data.d")
        a.run("service.d")
        self.assertEqual(len(a.repairs()), 2)
        self.assertGreaterEqual(len(a.calls("flock", "-n 9")), 2)

    def test_busy_kernel_lock_skips_work_then_releases_after_process_death(self):
        a = self.android
        state = a.path("/data/adb/load_drivers-state/boot-one")
        state.mkdir(parents=True)
        holder = subprocess.Popen([
            sys.executable, "-u", "-c",
            "import fcntl,sys,time; f=open(sys.argv[1],'w'); "
            "fcntl.flock(f,fcntl.LOCK_EX); print('locked'); time.sleep(60)",
            str(state / "lock"),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            a.run("post-fs-data.d")
            self.assertEqual(a.calls("lsmod"), [])
            self.assertEqual(a.calls("sleep"), [])
        finally:
            holder.kill()
            holder.communicate(timeout=5)
        # Kill releases the kernel lock without removing the stale file.
        a.run("post-fs-data.d")
        self.assertEqual(len(a.repairs()), 1)

    def test_flock_unavailable_fails_closed_without_side_effects(self):
        a = self.android
        a.configure(fail=["flock"])
        a.run("post-fs-data.d")
        self.assertEqual(a.calls("lsmod"), [])
        self.assertEqual(a.repairs(), [])
        self.assertEqual(a.calls("sysctl"), [])
        process = a.start()
        process.communicate(timeout=15)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(a.calls("lsmod"), [])
        self.assertEqual(a.calls("sysctl"), [])

    def test_failed_hal_restart_is_pending_until_success(self):
        a = self.android
        a.configure(hal_running=True, fail=["pkill"])
        a.run()
        a.configure(hal_running=True)
        a.run()
        a.run()
        for pattern in ("android.hardware.wifi-service", "android.hardware.biometrics.fingerprint"):
            self.assertEqual(len(a.calls("pkill", pattern)), 2)
        self.assertEqual(len(a.calls("modprobe", "dhd")), 1)

    def test_first_fod_command_failure_not_hidden_by_second_success(self):
        a = self.android
        a.configure(hal_running=True, fod_first_fails=True)
        a.run()
        self.assertEqual(a.calls("pkill", "android.hardware.biometrics.fingerprint"), [])
        a.configure(hal_running=True)
        a.run()
        a.run()
        self.assertEqual(len(a.repairs()), 2)
        self.assertEqual(len(a.calls("pkill", "android.hardware.biometrics.fingerprint")), 1)

    def test_deliberately_disabled_wifi_is_never_forced_on(self):
        self.android.run()
        self.assertEqual(self.android.calls("cmd"), [])

    def test_early_stage_never_sleeps_even_when_nodes_missing(self):
        a = self.android
        for name in ("qbt2000_fd", "qbt2000_ipc"):
            a.path("/dev/" + name).unlink()
        a.run("post-fs-data.d")
        self.assertEqual(a.calls("sleep"), [])

    def test_late_wait_is_small_and_bounded(self):
        a = self.android
        for name in ("qbt2000_fd", "qbt2000_ipc"):
            a.path("/dev/" + name).unlink()
        a.run()
        sleeps = a.calls("sleep")
        self.assertGreater(len(sleeps), 0)
        self.assertLessEqual(len(sleeps), 10)
        self.assertLessEqual(sum(float(c[1]) for c in sleeps), 2)

    def test_hal_not_running_before_load_is_not_killed(self):
        a = self.android
        a.configure(hal_running=False)
        a.run()
        self.assertEqual(a.calls("pkill"), [])

    def test_running_hal_restarts_only_after_new_module(self):
        a = self.android
        a.configure(hal_running=True)
        a.run()
        a.run("boot-completed.d")
        self.assertEqual(len(a.calls("pkill", "android.hardware.wifi-service")), 1)
        self.assertEqual(len(a.calls("pkill", "android.hardware.biometrics.fingerprint")), 1)
        calls = a.calls()
        for module, pattern in (("dhd", "android.hardware.wifi-service"), ("fingerprint", "android.hardware.biometrics.fingerprint")):
            load_index = next(i for i, call in enumerate(calls) if call[0] == "modprobe" and call[-1] == module)
            self.assertTrue(any(call[0] == "pgrep" and pattern in " ".join(call) for call in calls[:load_index]))

    def test_patched_fingerprint_preferred_and_stock_fallback_preserved(self):
        a = self.android
        patched = a.path("/data/adb/modules_fixed/fingerprint.ko")
        patched.parent.mkdir(parents=True)
        patched.touch()
        a.run()
        self.assertEqual(len(a.calls("insmod", "fingerprint.ko")), 1)
        self.assertEqual([c for c in a.calls("modprobe") if c[-1] == "fingerprint"], [])
        self.assertTrue(a.calls("modprobe", "fingerprint_sysfs"))
        patched.unlink()
        a.set_modules("dhd\nfingerprint_sysfs\n")
        a.run()
        self.assertEqual(len([c for c in a.calls("modprobe") if c[-1] == "fingerprint"]), 1)

    def test_hardware_repairs_preserved(self):
        a = self.android
        a.run()
        self.assertTrue(a.calls("chmod", "0660 " + str(a.path("/dev/qbt2000"))))
        self.assertTrue(a.calls("chown", "system:radio " + str(a.path("/sys/class/fingerprint/fingerprint"))))
        self.assertIn("fod_enable,1", self.source)
        self.assertEqual(a.path("/sys/class/sec/tsp/cmd").read_text().strip(), "set_fod_rect,426,1675,654,1903")
        for suffix in ("fp/User_0", "ta", "bk", "meta", "log"):
            self.assertTrue(a.path("/data/vendor/biometrics/" + suffix).is_dir())
        for suffix in ("bg_estimation", "calib_test", "template"):
            self.assertTrue(a.path("/data/vendor/misc/qti_fp/" + suffix).is_dir())
        self.assertTrue(a.calls("chcon", "u:object_r:biometrics_vendor_data_file:s0"))
        self.assertEqual(len(a.calls("ksud", "resetprop -d ro.vendor.fingerprint.uses_percentage_samples")), 1)
        self.assertEqual(len(a.calls("ksud", "resetprop -d ro.vendor.fingerprint.cancel_on_enroll_completion")), 1)

    @unittest.skipIf(LEGACY_TUNING, "deployment intentionally retains legacy tuning")
    def test_tuning_values_preserved(self):
        a = self.android
        a.run()
        expected = {"net.ipv4.tcp_mtu_probing=1", "kernel.sched_schedstats=0", "vm.page-cluster=0"}
        self.assertEqual({c[-1] for c in a.calls("sysctl")}, expected)
        self.assertEqual(a.path("/sys/block/zram0/queue/read_ahead_kb").read_text().strip(), "0")
        for command in ("iptables", "ip6tables"):
            self.assertTrue(a.calls(command, "-A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu"))

    def test_module_checks_and_completed_repeats_do_not_spawn_helpers(self):
        a = self.android
        a.run()
        calls = a.calls()
        a.run()
        self.assertEqual(a.calls("lsmod"), [])
        self.assertEqual([call for call in a.calls() if call[0] != "flock"],
                         [call for call in calls if call[0] != "flock"])

    def test_module_still_initializing_is_not_treated_as_live(self):
        a = self.android
        a.set_modules("dhd\nfingerprint_sysfs\nfingerprint\n")
        a.path("/sys/module/dhd/initstate").write_text("coming\n")
        a.run()
        self.assertEqual(len(a.calls("modprobe", "dhd")), 1)

    def test_late_wait_includes_ipc_node(self):
        a = self.android
        a.path("/dev/qbt2000_ipc").unlink()
        a.run()
        self.assertEqual(len(a.calls("sleep")), 5)
        self.assertFalse(a.path("/data/adb/load_drivers-state/boot-one/service.repairs").exists())

    @unittest.skipIf(LEGACY_TUNING, "deployment intentionally retains legacy tuning")
    def test_unsupported_scheduler_stats_do_not_repeat_other_tuning(self):
        a = self.android
        a.path("/proc/sys/kernel/sched_schedstats").unlink()
        a.run()
        a.run()
        self.assertEqual(a.calls("sysctl", "kernel.sched_schedstats"), [])
        self.assertEqual(len(a.calls("sysctl", "net.ipv4.tcp_mtu_probing=1")), 1)
        self.assertTrue(a.path("/data/adb/load_drivers-state/boot-one/tuning").is_file())

    @unittest.skipIf(LEGACY_TUNING, "deployment intentionally retains legacy tuning")
    def test_absent_ipv6_does_not_block_success(self):
        a = self.android
        a.path("/proc/sys/net/ipv6").rmdir()
        a.run()
        self.assertEqual(a.calls("ip6tables"), [])
        self.assertTrue(a.path("/data/adb/load_drivers-state/boot-one/tuning").is_file())

    @unittest.skipIf(LEGACY_TUNING, "deployment intentionally retains legacy tuning")
    def test_failed_firewall_family_retries_without_replaying_successes(self):
        a = self.android
        a.configure(fail=["ip6tables"])
        a.run()
        ipv4 = a.calls("iptables")
        settings = a.calls("sysctl")
        a.configure()
        a.run()
        a.run()
        self.assertEqual(a.calls("iptables"), ipv4)
        self.assertEqual(a.calls("sysctl"), settings)
        self.assertEqual(len(a.calls("ip6tables", "-A POSTROUTING")), 2)
        self.assertTrue(all(call[1:3] == ["-w", "1"] for call in a.calls("ip6tables")))


if __name__ == "__main__":
    unittest.main()
