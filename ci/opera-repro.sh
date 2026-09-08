#!/usr/bin/env bash
# Reproduce OpenIPC/majestic-webui#317 on real arm64 Opera (emulator already
# booted by the runner action). Serves the autoplay probe, drives Opera to it,
# injects a tap as the user gesture, and reads the verdict the page POSTs back.
#
# PLAYER selects which page:
#   autoplay.html  raw muted-MediaStream autoplay probe (the mechanism)
set -uo pipefail
PAGE="${PAGE:-autoplay.html}"
PORT="${PORT:-8080}"

echo "=== ABI (must be arm64 native) ==="
adb shell getprop ro.product.cpu.abi

echo "=== serve the probe on the host (emulator reaches it at 10.0.2.2) ==="
python3 ci/serve.py "$PORT" web >serve.log 2>&1 &
SERVE_PID=$!
sleep 2
curl -sf "http://localhost:$PORT/$PAGE" >/dev/null && echo "server up" || { echo "server DOWN"; cat serve.log; exit 2; }

echo "=== install arm64 Opera ==="
UA='Mozilla/5.0 (Linux; Android 14; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36'
curl -fsSL -A "$UA" -o opera.xapk "https://d.apkpure.com/b/XAPK/com.opera.browser?version=latest"
mkdir -p opera_apks && unzip -o -q opera.xapk -d opera_apks
adb install-multiple -r opera_apks/*.apk
adb logcat -c

URL="http://10.0.2.2:$PORT/$PAGE"
echo "=== launch Opera at $URL ==="
adb shell am start -a android.intent.action.VIEW -d "$URL" com.opera.browser
# Opera may show a first-run/consent screen; dismiss common buttons if present.
sleep 12
adb exec-out screencap -p > 01-loaded.png || true

# Wait for the page to signal it is ready for the gesture (POSTs readyForGesture).
echo "=== wait for readyForGesture ==="
for i in $(seq 1 40); do
  if [ -f result.json ] && grep -q '"readyForGesture": *true' result.json; then echo "ready"; break; fi
  sleep 1
done
cat result.json 2>/dev/null || echo "(no interim result yet)"

echo "=== inject a user gesture (tap centre of the video) ==="
# pixel_6 is 1080x2400; the video is the top 55vh — tap well inside it.
adb shell input tap 540 700
sleep 1
adb shell input tap 540 700
sleep 6
adb exec-out screencap -p > 02-after-tap.png || true

echo "=== final verdict ==="
for i in $(seq 1 20); do
  if [ -f result.json ] && grep -q '"done": *true' result.json; then break; fi
  sleep 1
done
adb logcat -d > logcat.txt || true
kill $SERVE_PID 2>/dev/null || true

echo "---- result.json ----"
cat result.json 2>/dev/null || { echo "NO RESULT — page never reported"; exit 2; }
echo
python3 - <<'PY'
import json, sys
try:
    r = json.load(open('result.json'))
except Exception as e:
    print("HARNESS: no/invalid result:", e); sys.exit(2)
pa, pb = r.get('playPromiseA'), r.get('playPromiseB')
noG, afterG = r.get('playedNoGesture'), r.get('playedAfterGesture')
print("ua:", r.get('ua',''))
print("playPromiseA=%r playPromiseB=%r playedNoGesture=%r playedAfterGesture=%r gestureSeen=%r"
      % (pa, pb, noG, afterG, r.get('gestureSeen')))
if r.get('reproduced'):
    extra = ""
    if pa == 'resolved' and not noG:
        extra = " — and play() RESOLVED without playing, so a reject-gated gesture-retry never arms (explains #396)."
    print("\nRESULT: REPRODUCED — muted autoplay did NOT start on its own%s" % extra)
    if afterG:
        print("A user gesture DID start it, so the fix is a gesture-retry that does not depend on play() rejecting.")
    sys.exit(1)
print("\nRESULT: muted autoplay started on its own — not reproduced on this Opera/build.")
sys.exit(0)
PY
