"""Emulator lifecycle: launch (optionally with pcap capture), wait, stop."""
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk"
EMULATOR = SDK / "emulator" / "emulator"
ADB = SDK / "platform-tools" / "adb"

BASE_ARGS = ["-no-window", "-gpu", "swiftshader_indirect", "-no-audio",
             "-no-boot-anim", "-accel", "on"]


def env():
    e = os.environ.copy()
    e["ANDROID_SDK_ROOT"] = str(SDK)
    e["ANDROID_HOME"] = str(SDK)
    return e


class Emulator:
    def __init__(self, avd="qa", pcap=None, extra_args=None, log_path=None):
        self.avd = avd
        self.pcap = pcap
        self.extra_args = extra_args or []
        self.log_path = log_path
        self.proc = None

    def start(self):
        args = [str(EMULATOR), "-avd", self.avd, *BASE_ARGS, *self.extra_args]
        if self.pcap:
            args += ["-tcpdump", str(self.pcap)]
        log = open(self.log_path, "wb") if self.log_path else subprocess.DEVNULL
        self.proc = subprocess.Popen(args, env=env(), stdout=log, stderr=log,
                                     start_new_session=True)
        return self.proc

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout=30):
        subprocess.run([str(ADB), "-s", "emulator-5554", "emu", "kill"],
                       env=env(), capture_output=True, timeout=timeout)
        if self.proc:
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def wait_adb_device(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run([str(ADB), "devices"], env=env(),
                             capture_output=True, text=True).stdout
        if "emulator-5554\tdevice" in out:
            return True
        time.sleep(2)
    raise TimeoutError(f"no emulator in adb devices:\n{out}")
