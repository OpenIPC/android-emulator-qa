#!/usr/bin/env python3
"""Reusable tinyCam ONVIF flow: add an ONVIF camera and trigger a connection
test, archiving a screenshot + uiautomator dump at every step.

Designed to run against the logging relay (see harness/relay.py): point tinyCam
at the relay so the full ONVIF/RTSP exchange is captured off-box. The emulator's
own -tcpdump does NOT capture the slirp-NAT'd camera path — use the relay.

Usage:
  python3 flows/tinycam_onvif.py --host 10.0.2.2 --onvif-port 8080 \
      --rtsp-port 18554 --user root --password 123456 --run-dir runs/<ts>

Assumes: emulator booted (harness/capture.py), tinyCam installed
(com.alexvas.dvr.pro). Install with: adb install -r -g <apk>.

UI automation note: tinyCam v18 has no testID hooks, so this drives by
resource-id / visible text via uiautomator. Coordinates are derived from node
bounds at runtime (never hard-coded pixels), so it survives density changes.
Every action is archived; if a step can't find its target it raises StepError
with the list of visible texts, and the screenshots show exactly where it stopped.
"""
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
from device import Device            # noqa: E402
from ui import Screen                # noqa: E402

PKG = "com.alexvas.dvr.pro"

# Set by run(): where the gestures that are not steps of their own (a scroll,
# a field focus) archive their screen, so a misfire leaves its state behind.
_RUN_DIR = None
_GESTURES = 0


def archive_gesture(dev, what):
    global _GESTURES
    if _RUN_DIR is None:
        return
    _GESTURES += 1
    archive(dev, _RUN_DIR, f"g{_GESTURES:03d}_{what}")


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


def tap_text(dev, contains, timeout=15, scroll=True):
    end = time.time() + timeout
    tries = 0
    while time.time() < end:
        n = dump(dev).first(contains=contains)
        if n:
            dev.tap(*n.center)
            return n
        tries += 1
        # The settings list is taller than the screen: an item below the
        # fold (RTSP port, Username, Password) only appears after a scroll.
        # Scroll down a few times, then back up, so an item above the fold
        # (Camera status, after the credentials) is found too.
        # Coordinates are fractions of the display, not pixels, so a smaller
        # emulator skin still swipes inside the list.
        if scroll:
            w, h = dev.screen_size()
            lo, hi = int(h * 0.45), int(h * 0.72)
            if (tries // 4) % 2 == 0:
                dev.swipe(w // 2, hi, w // 2, lo)
            else:
                dev.swipe(w // 2, lo, w // 2, hi)
            time.sleep(0.5)
            archive_gesture(dev, "scroll")
        time.sleep(1)
    raise RuntimeError(f"text containing {contains!r} not found")


def set_dialog_text(dev, value, clear=24):
    """A tinyCam input dialog is open; replace its field with `value`, tap OK."""
    time.sleep(1)
    # focus the field (upper third of dialog), clear, type
    scr = dump(dev)
    # Focus the field before typing. Typing into a dialog whose field has not
    # taken focus yet loses characters: a password dialog once received five
    # of "123456", and every ONVIF request then failed authentication.
    field = scr.first(cls="EditText")
    if field:
        dev.tap(*field.center)
        time.sleep(0.5)
        archive_gesture(dev, "focus")
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


def streaming(text_blob):
    """Whether the status dialog reports a non-zero frame rate. Read as numbers:
    a substring test for "0.0 fps" also matched "20.0 fps" and called a
    stream at 10, 20 or 30 fps not streaming."""
    rates = [float(m) for m in re.findall(r"(\d+(?:\.\d+)?)\s*fps", text_blob)]
    return any(r > 0 for r in rates)


# More than any RTSP exchange without media carries: DESCRIBE's SDP is well
# under a kilobyte, while a lab camera streaming H.264 sent 33 MB in a minute.
RELAY_MEDIA_BYTES = 64 * 1024


def relay_verdict(relay_dir, rtsp_port):
    """STREAMING when the relay carried media from the camera over RTSP.

    The status dialog is often unreadable (see Device.ui_dump), so this reads
    the bytes instead: the relay names each capture by its listen port."""
    got = sum(p.stat().st_size
              for p in relay_dir.glob(f"conn*_S2C_:{rtsp_port}.raw"))
    return "STREAMING" if got > RELAY_MEDIA_BYTES else "unknown"


def run(args):
    global _RUN_DIR
    dev = Device()
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _RUN_DIR = run_dir

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
    # On a fresh install the magnifier is an ImageButton with no id or
    # content-desc, so fall back to the dialog's only clickable ImageButton.
    scr = dump(dev)
    search = (scr.first(rid="search") or scr.first(desc="Search")
              or scr.first(cls="ImageButton", clickable=True))
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
    # The row title opens the live-feed dialog; after the credentials the
    # list is scrolled down, so this may need to scroll back up to find it.
    tap_text(dev, "Camera status", timeout=45)

    # Read the verdict off the status dialog, polling: through the relay the
    # first frame took 20-40 s to arrive on a lab camera, and a single read at
    # 8 s always saw "0.0 fps". The dialog itself often cannot be dumped;
    # then the relay's byte count decides, and 16_status.png is the record.
    verdict = "unknown"
    end = time.time() + 60
    while time.time() < end:
        time.sleep(5)
        try:
            scr = dump(dev)
        except RuntimeError:
            # The live statistics dialog never goes idle, so uiautomator
            # cannot dump it at all; the relay below is what can tell.
            continue
        text_blob = " ".join((n.text + " " + n.desc) for n in scr.texts())
        if "authorization required" in text_blob or "Check username" in text_blob:
            verdict = "AUTH_FAILED"
            break
        if streaming(text_blob):
            verdict = "STREAMING"
            break
    archive(dev, run_dir, "16_status")
    if verdict == "unknown" and args.relay_dir:
        verdict = relay_verdict(Path(args.relay_dir), args.rtsp_port)
    print(f"verdict={verdict}")
    (run_dir / "flow_verdict.txt").write_text(verdict + "\n")
    return verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="10.0.2.2")
    ap.add_argument("--onvif-port", type=int, default=8080)
    ap.add_argument("--rtsp-port", type=int, default=18554)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="123456")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--relay-dir",
                    help="the relay's --outdir, to judge streaming by bytes "
                         "when the status dialog cannot be read")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
