"""Thin adb wrapper for driving an emulator instance."""
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk"
ADB = SDK / "platform-tools" / "adb"


class Device:
    def __init__(self, serial="emulator-5554"):
        self.serial = serial

    def adb(self, *args, timeout=60, check=True, binary=False):
        cmd = [str(ADB), "-s", self.serial, *args]
        res = subprocess.run(cmd, capture_output=not binary, timeout=timeout)
        if check and res.returncode != 0:
            raise RuntimeError(
                f"adb {' '.join(args)} failed rc={res.returncode}: "
                f"{(res.stderr or b'').decode(errors='replace')[:500]}"
            )
        return (res.stdout or b"").decode(errors="replace")

    def shell(self, cmd, timeout=60, check=True):
        return self.adb("shell", cmd, timeout=timeout, check=check)

    def wait_boot(self, timeout=600):
        deadline = time.time() + timeout
        self.adb("wait-for-device", timeout=timeout)
        while time.time() < deadline:
            out = self.shell("getprop sys.boot_completed", check=False).strip()
            if out == "1":
                # settle: package manager up
                if "package:" in self.shell("pm list packages android", check=False):
                    return True
            time.sleep(3)
        raise TimeoutError("device did not finish booting")

    def install(self, apk_path):
        return self.adb("install", "-r", "-g", str(apk_path), timeout=600)

    def screencap(self, out_path):
        png = subprocess.run(
            [str(ADB), "-s", self.serial, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=60,
        ).stdout
        Path(out_path).write_bytes(png)
        return out_path

    def ui_dump(self):
        """Return uiautomator window dump XML as a string."""
        for _ in range(3):
            out = self.shell(
                "uiautomator dump /sdcard/ui.xml >/dev/null 2>&1; cat /sdcard/ui.xml",
                check=False, timeout=30,
            )
            if out.lstrip().startswith("<?xml"):
                return out
            time.sleep(1)
        raise RuntimeError(f"uiautomator dump failed: {out[:200]}")

    def tap(self, x, y):
        self.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1, y1, x2, y2, ms=300):
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {ms}")

    def text(self, s):
        # input text: spaces must be %s, escape shell-sensitive chars
        esc = s.replace("\\", "\\\\").replace(" ", "%s")
        for ch in "()<>|;&*~\"'`$":
            esc = esc.replace(ch, "\\" + ch)
        self.shell(f"input text {esc}")

    def key(self, code):
        self.shell(f"input keyevent {code}")

    def back(self):
        self.key(4)

    def logcat_dump(self, out_path):
        out = self.adb("logcat", "-d", timeout=120)
        Path(out_path).write_text(out)

    def logcat_clear(self):
        self.adb("logcat", "-c", check=False)
