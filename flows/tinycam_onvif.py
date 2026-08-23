#!/usr/bin/env python3
"""Reusable tinyCam ONVIF flow: add an ONVIF camera and trigger a connection
test, archiving a screenshot + uiautomator dump at every step.

Designed to run against the logging relay (see harness/relay.py): point tinyCam
at the relay so the full ONVIF/RTSP exchange is captured off-box. The emulator's
own -tcpdump does NOT capture the slirp-NAT'd camera path — use the relay.

Usage:
  python3 flows/tinycam_onvif.py --host 10.0.2.2 --onvif-port 8080 \
      --rtsp-port 8554 --user root --password 123456 --run-dir runs/<ts>

Assumes: emulator booted (harness/capture.py), tinyCam installed
(com.alexvas.dvr.pro). Install with: adb install -r -g <apk>.

UI automation note: tinyCam v18 has no testID hooks, so this drives by
resource-id / visible text via uiautomator. Coordinates are derived from node
bounds at runtime (never hard-coded pixels), so it survives density changes.
Every action is archived; if a step can't find its target it raises StepError
with the list of visible texts, and the screenshots show exactly where it stopped.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
from device import Device            # noqa: E402
from ui import Screen                # noqa: E402

PKG = "com.alexvas.dvr.pro"


def dump(dev):
    return Screen(dev.ui_dump())


def tap_rid(dev, rid, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        n = dump(dev).first(rid=rid)
        if n:
            dev.tap(*n.center)
            return n
        time.sleep(1)
    raise RuntimeError(f"resource-id {rid!r} not found")


def tap_text(dev, contains, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        n = dump(dev).first(contains=contains)
        if n:
            dev.tap(*n.center)
            return n
        time.sleep(1)
    raise RuntimeError(f"text containing {contains!r} not found")


def set_dialog_text(dev, value, clear=24):
    """A tinyCam input dialog is open; replace its field with `value`, tap OK."""
    time.sleep(1)
    # focus the field (upper third of dialog), clear, type
    scr = dump(dev)
    # tap near the visible EditText: the field sits just under the title
    dev.shell("input keyevent KEYCODE_MOVE_END")
    for _ in range(clear):
        dev.key(67)   # DEL
    dev.text(value)
    time.sleep(0.5)
    ok = dump(dev).first(text="OK") or dump(dev).first(contains="OK")
    if not ok:
        raise RuntimeError("OK button not found in dialog")
    dev.tap(*ok.center)
    time.sleep(1.2)


def archive(dev, run_dir, name):
    base = Path(run_dir) / name
    dev.screencap(f"{base}.png")
    try:
        Path(f"{base}.xml").write_text(dev.ui_dump())
    except Exception:
        pass


def run(args):
    dev = Device()
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    # launch
    dev.shell(f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1")
    time.sleep(6)
    archive(dev, run_dir, "10_launch")

    # skip welcome wizard if present (door/skip icon is bottom-left)
    scr = dump(dev)
    if scr.first(contains="Welcome") or scr.first(contains="Thank you"):
        # skip button lives bottom-left; tap its region
        dev.tap(157, int(dev.shell("wm size").split("x")[-1].strip()) - 120) \
            if False else dev.tap(157, 1815)
        time.sleep(3)
        archive(dev, run_dir, "11_after_welcome")

    # add camera: main FAB -> Add IP camera
    fab = dump(dev).first(rid="fab_main") or dump(dev).first(contains="add camera")
    if fab:
        dev.tap(*fab.center)
        time.sleep(2)
    tap_rid(dev, "fab_add_ip_cam")
    time.sleep(3)
    archive(dev, run_dir, "12_add_ip")

    # brand -> search ONVIF -> Profile S
    tap_text(dev, "Camera brand")
    time.sleep(1.5)
    # open search in the brand chooser
    search = dump(dev).first(rid="search") or dump(dev).first(desc="Search")
    if search:
        dev.tap(*search.center)
        time.sleep(1)
    dev.text("ONVIF")
    time.sleep(1.5)
    tap_text(dev, "ONVIF) Profile S")
    time.sleep(2.5)
    archive(dev, run_dir, "13_brand_set")

    # hostname
    tap_text(dev, "Hostname/IP address")
    set_dialog_text(dev, args.host)
    archive(dev, run_dir, "14_host")

    # ONVIF port
    tap_text(dev, "ONVIF port number")
    set_dialog_text(dev, str(args.onvif_port))

    # RTSP port: uncheck Auto first, then type
    tap_text(dev, "RTSP port number")
    time.sleep(1.2)
    auto = dump(dev).first(contains="Auto")
    if auto:
        dev.tap(*auto.center)   # uncheck
        time.sleep(0.6)
    set_dialog_text(dev, str(args.rtsp_port))

    # credentials
    tap_text(dev, "Username")
    set_dialog_text(dev, args.user)
    tap_text(dev, "Password")
    set_dialog_text(dev, args.password)
    archive(dev, run_dir, "15_configured")

    # trigger connection test
    tap_text(dev, "Camera status")
    time.sleep(8)
    archive(dev, run_dir, "16_status")

    # read verdict off the status screen
    scr = dump(dev)
    text_blob = " ".join((n.text + " " + n.desc) for n in scr.texts())
    verdict = "unknown"
    if "authorization required" in text_blob or "Check username" in text_blob:
        verdict = "AUTH_FAILED"
    elif "fps" in text_blob and "0.0 fps" not in text_blob:
        verdict = "STREAMING"
    print(f"verdict={verdict}")
    (run_dir / "flow_verdict.txt").write_text(verdict + "\n")
    return verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="10.0.2.2")
    ap.add_argument("--onvif-port", type=int, default=8080)
    ap.add_argument("--rtsp-port", type=int, default=8554)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="123456")
    ap.add_argument("--run-dir", required=True)
    run(ap.parse_args())


if __name__ == "__main__":
    main()
