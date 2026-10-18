#!/usr/bin/env python3
"""PixelPilot smoke flow: install the APK, launch it, prove it survives start-up
with no USB adapter attached, and (optionally) feed it an H.264 RTP stream over
UDP to exercise the videonative decoder end to end.

PixelPilot ships arm64-v8a/armeabi-v7a native code only, so on the x86_64 QA
image everything JNI runs under ARM translation (libndk_translation). The point
of this flow is to find out whether that works at all for a given build, and to
give CI a hardware-free check that the receive -> parse -> MediaCodec path
still comes up.

Usage:
  python3 flows/pixelpilot_smoke.py --apk PixelPilot-debug.apk \
      --run-dir runs/<ts>-pixelpilot [--stream 15]

Assumes: emulator booted (make boot && make wait), ffmpeg on the host when
--stream is used. Every step archives a screenshot + uiautomator dump under
--run-dir; the verdict lands in verdict.json.

Exit code: 0 pass, 1 the app failed the smoke test, 2 harness/usage error.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
from device import ADB, Device      # noqa: E402
from runner import Runner           # noqa: E402

PKG = "com.openipc.pixelpilot"
ACTIVITY = f"{PKG}/.VideoActivity"
VIDEO_PORT = 5600

# Text VideoActivity shows once WfbLinkManager finds no supported adapter.
NO_ADAPTER_TEXT = "No compatible wifi adapter found"

# Crash signatures in logcat, scoped to the app: an API-34 emulator cold boot
# has its own crash-looping system services (Bluetooth), which must not count.
CRASH_PATTERNS = [
    r"FATAL EXCEPTION.*\n.*Process: " + re.escape(PKG),
    r">>> " + re.escape(PKG) + r" <<<",                 # tombstone header
    r"Process " + re.escape(PKG) + r" .*has died",
    r"Force finishing activity " + re.escape(PKG),
]

# videonative decoder milestones (VideoDecoder.cpp log lines).
DECODER_CONFIGURED = r"AMediaCodec_configure: OK"
DECODER_OUTPUT = r"Actual Width and Height in output"


def log(msg):
    print(f"[pixelpilot_smoke] {msg}", flush=True)


def app_pid(dev):
    out = dev.shell(f"pidof {PKG}", check=False).strip()
    return int(out.split()[0]) if out else None


def scan_logcat(text, pid=None):
    hits = []
    for pat in CRASH_PATTERNS:
        for m in re.finditer(pat, text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            hits.append(text[line_start:line_end if line_end > 0 else None][:300])
    if pid:
        # Fatal signals are logged by the dying process itself under its pid.
        for m in re.finditer(r"^.*\s%d\s+\d+\s+F\s+libc\s*:.*Fatal signal.*$" % pid, text, re.M):
            hits.append(m.group(0)[:300])
    return hits


def dismiss_system_error_dialog(run, scr):
    """'<X> keeps stopping' / 'isn't responding' dialogs from unrelated system
    apps stack on top of everything on a cold-booted emulator. Close them."""
    if scr and (scr.first(contains="keeps stopping") or scr.first(contains="isn't responding")):
        btn = scr.first(rid="aerr_close") or scr.first(rid="aerr_wait") or scr.first(text="Close app")
        if btn:
            run.dev.tap(*btn.center)
            log("dismissed a system error dialog")
            time.sleep(1.5)
            return True
    return False


def accept_vpn_consent(run, scr):
    """VideoActivity calls VpnService.prepare() in onCreate, which raises the
    system 'Connection request' dialog (package com.android.vpndialogs) on
    first run. Accept it; return True if a tap was made."""
    if scr and scr.first(contains="Connection request"):
        ok = scr.first(rid="button1") or scr.first(text="OK")
        if ok:
            run.dev.tap(*ok.center)
            log("accepted VPN consent dialog")
            time.sleep(2.5)
            return True
    return False


def start_stream(seconds, width=1280, height=720, fps=30):
    """Push a synthetic H.264 RTP stream to the emulator's UDP 5600 via a host
    port redirection. SPS/PPS are repeated on every keyframe so the receiver
    can start decoding mid-stream, like a real wfb-ng feed."""
    subprocess.run([str(ADB), "emu", "redir", "add",
                    f"udp:{VIDEO_PORT}:{VIDEO_PORT}"], check=False,
                   capture_output=True, timeout=30)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
        "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}",
        "-t", str(seconds),
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-g", str(fps), "-x264-params", "repeat-headers=1",
        "-pix_fmt", "yuv420p",
        "-f", "rtp", f"rtp://127.0.0.1:{VIDEO_PORT}",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def run_flow(args):
    run_dir = Path(args.run_dir)
    run = Runner(run_dir, serial=args.serial)
    dev = run.dev
    verdict = {"apk": str(args.apk), "serial": args.serial, "checks": {}}

    def check(name, ok, detail=None):
        verdict["checks"][name] = {"ok": bool(ok), "detail": detail}
        log(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
        return ok

    # --- install & launch -------------------------------------------------
    dev.shell(f"am force-stop {PKG}", check=False)
    # Keep unrelated system crash/ANR dialogs from covering the app.
    dev.shell("settings put global hide_error_dialogs 1", check=False)
    dev.logcat_clear()
    log(f"installing {args.apk}")
    dev.install(args.apk)
    abi = dev.shell(f"dumpsys package {PKG} | grep -m1 primaryCpuAbi", check=False).strip()
    verdict["primaryCpuAbi"] = abi
    log(abi or "primaryCpuAbi not reported")

    dev.shell(f"am start -W -n {ACTIVITY}", timeout=60)
    time.sleep(3)
    run.snap("after_launch")

    # --- survives start-up -------------------------------------------------
    # One loop handles everything that can sit on top of the activity: the VPN
    # consent (which the activity re-requests on resume until granted) and
    # unrelated system error dialogs, until the no-adapter message shows.
    matched = None
    deadline = time.time() + args.timeout
    while time.time() < deadline and matched is None:
        scr = run.snap("wait_no_adapter")
        if scr is None:
            time.sleep(2)
            continue
        if dismiss_system_error_dialog(run, scr):
            continue
        if accept_vpn_consent(run, scr):
            continue
        for cand in (NO_ADAPTER_TEXT, "wifi adapter"):
            if scr.first(contains=cand):
                matched = cand
                break
        if matched is None:
            time.sleep(2)
    pid = app_pid(dev)
    check("process_alive", pid is not None, f"pid={pid}")
    check("no_adapter_message", matched is not None,
          None if matched else "message never appeared; see last wait_*.png")

    logcat_path = run_dir / "logcat_startup.txt"
    dev.logcat_dump(logcat_path)
    crashes = scan_logcat(logcat_path.read_text(errors="replace"), pid)
    check("no_native_crash", not crashes, crashes[:5] if crashes else None)

    startup_ok = all(c["ok"] for c in verdict["checks"].values())

    # --- optional: decode a synthetic stream ------------------------------
    if args.stream > 0 and startup_ok:
        log(f"streaming synthetic H.264 for {args.stream}s to udp:{VIDEO_PORT}")
        dev.logcat_clear()
        proc = start_stream(args.stream)
        deadline = time.time() + args.stream + 10
        configured = output = False
        while time.time() < deadline and not (configured and output):
            time.sleep(2)
            text = dev.adb("logcat", "-d", timeout=60)
            configured = configured or re.search(DECODER_CONFIGURED, text) is not None
            output = output or re.search(DECODER_OUTPUT, text) is not None
            if app_pid(dev) is None:
                break
        run.snap("while_streaming")
        proc.terminate()
        try:
            _, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            err = b""
        if err:
            (run_dir / "ffmpeg.err").write_bytes(err)

        stream_log = run_dir / "logcat_stream.txt"
        dev.logcat_dump(stream_log)
        crashes = scan_logcat(stream_log.read_text(errors="replace"), pid)
        check("decoder_configured", configured)
        check("decoder_output_frames", output)
        check("no_native_crash_while_streaming", not crashes, crashes[:5] if crashes else None)
        check("process_alive_after_stream", app_pid(dev) is not None)

    verdict["pass"] = all(c["ok"] for c in verdict["checks"].values())
    (run_dir / "verdict.json").write_text(json.dumps(verdict, indent=1))
    log(f"verdict: {'PASS' if verdict['pass'] else 'FAIL'} -> {run_dir / 'verdict.json'}")
    return 0 if verdict["pass"] else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apk", required=True, type=Path)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--serial", default="emulator-5554")
    ap.add_argument("--timeout", type=int, default=45,
                    help="seconds to wait for the no-adapter message")
    ap.add_argument("--stream", type=int, default=0,
                    help="seconds of synthetic H.264 to push over UDP 5600 (0 = skip)")
    args = ap.parse_args()
    if not args.apk.is_file():
        log(f"no such APK: {args.apk}")
        return 2
    try:
        return run_flow(args)
    except Exception as e:  # harness failure, not an app verdict
        log(f"harness error: {e!r}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
