#!/usr/bin/env bash
# Runs INSIDE the emulator-runner step (emulator already booted). Proves that
# real arm64 Opera for Android runs on an arm64 emulator — the thing the x86_64
# lab emulator could not do (it crashed in libndk_translation, see
# findings/2026-09-06-opera-android-arm-translation-crash.md). On an
# Apple-silicon macOS runner the emulator is arm64-native, so Opera needs no
# translation.
set -uo pipefail

echo "=== emulator ABI ==="
adb shell getprop ro.product.cpu.abilist
adb shell getprop ro.product.cpu.abi

echo "=== fetch Opera arm64 XAPK ==="
UA='Mozilla/5.0 (Linux; Android 14; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36'
curl -fsSL -A "$UA" -o opera.xapk "https://d.apkpure.com/b/XAPK/com.opera.browser?version=latest"
ls -la opera.xapk
# An XAPK is a zip of base.apk + config.arm64_v8a.apk (+ others).
mkdir -p opera_apks
unzip -o -q opera.xapk -d opera_apks
echo "APKs in the XAPK:"; ls -1 opera_apks/*.apk

echo "=== install Opera (all splits) ==="
adb install-multiple -r opera_apks/*.apk

echo "=== launch Opera at a data: page (no network needed for the crash test) ==="
adb logcat -c
# LAUNCHER first; if that path differs, monkey falls back to whatever it is.
adb shell am start -a android.intent.action.VIEW -d 'data:text/html,<h1>opera-alive</h1>' com.opera.browser \
  || adb shell monkey -p com.opera.browser -c android.intent.category.LAUNCHER 1
sleep 20
adb exec-out screencap -p > opera-launch.png || true

echo "=== crash check ==="
adb logcat -d > logcat.txt
CRASH=$(grep -iE 'libndk_translation|SIGSEGV|Fatal signal|com\.opera\.browser.*died|FATAL EXCEPTION' logcat.txt | head -20)
if [ -n "$CRASH" ]; then
  echo "---- crash evidence ----"; echo "$CRASH"
fi

echo "=== is Opera still alive? ==="
if adb shell pidof com.opera.browser >/dev/null 2>&1; then
  echo "RESULT: OPERA ALIVE — arm64 Opera runs on the arm64 emulator."
  exit 0
else
  echo "RESULT: OPERA NOT RUNNING"
  [ -n "$CRASH" ] && echo "(crashed — see evidence above)"
  exit 1
fi
