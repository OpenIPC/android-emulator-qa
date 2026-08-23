#!/usr/bin/env bash
# Idempotent user-space bootstrap: JDK 17 + Android SDK (platform-tools, emulator,
# API 30 x86_64 image) + one AVD named "qa". No root required.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SDK="$ROOT/sdk"
JDK="$SDK/jdk"
CMDLINE_ZIP_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
JDK_URL="https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse"
IMAGE="system-images;android-30;google_apis;x86_64"

mkdir -p "$SDK"

log() { echo "[bootstrap] $*"; }

# --- JDK 17 ---
if [ ! -x "$JDK/bin/java" ]; then
    log "fetching Temurin JDK 17"
    wget -q --show-progress -O "$SDK/jdk.tar.gz" "$JDK_URL"
    mkdir -p "$JDK"
    tar -xzf "$SDK/jdk.tar.gz" -C "$JDK" --strip-components=1
    rm -f "$SDK/jdk.tar.gz"
fi
export JAVA_HOME="$JDK"
export PATH="$JAVA_HOME/bin:$PATH"
log "java: $(java -version 2>&1 | head -1)"

# --- cmdline-tools ---
SDKMANAGER="$SDK/cmdline-tools/latest/bin/sdkmanager"
if [ ! -x "$SDKMANAGER" ]; then
    log "fetching Android cmdline-tools"
    wget -q --show-progress -O "$SDK/cmdline-tools.zip" "$CMDLINE_ZIP_URL"
    rm -rf "$SDK/cmdline-tools"
    unzip -q "$SDK/cmdline-tools.zip" -d "$SDK"
    mkdir -p "$SDK/cmdline-tools-staging"
    mv "$SDK/cmdline-tools" "$SDK/cmdline-tools-staging/latest"
    mv "$SDK/cmdline-tools-staging" "$SDK/cmdline-tools"
    rm -f "$SDK/cmdline-tools.zip"
fi

export ANDROID_SDK_ROOT="$SDK"
export ANDROID_HOME="$SDK"

# --- SDK packages ---
if [ ! -x "$SDK/platform-tools/adb" ] || [ ! -x "$SDK/emulator/emulator" ] \
   || [ ! -d "$SDK/system-images/android-30/google_apis/x86_64" ]; then
    log "accepting licenses + installing packages (platform-tools, emulator, $IMAGE)"
    yes | "$SDKMANAGER" --licenses >/dev/null || true
    "$SDKMANAGER" "platform-tools" "emulator" "$IMAGE" "platforms;android-30"
fi
log "adb: $("$SDK/platform-tools/adb" --version | head -1)"
log "emulator: $("$SDK/emulator/emulator" -version 2>/dev/null | head -1 || echo n/a)"

# --- AVD ---
AVDMANAGER="$SDK/cmdline-tools/latest/bin/avdmanager"
if ! "$AVDMANAGER" list avd 2>/dev/null | grep -q "Name: qa"; then
    log "creating AVD 'qa'"
    echo no | "$AVDMANAGER" create avd -n qa -k "$IMAGE" --force
    AVD_CONFIG="$HOME/.android/avd/qa.avd/config.ini"
    # sensible phone-ish defaults for headless UI automation
    {
        echo "hw.ramSize=4096"
        echo "hw.lcd.width=1080"
        echo "hw.lcd.height=1920"
        echo "hw.lcd.density=420"
        echo "hw.keyboard=yes"
        echo "disk.dataPartition.size=8G"
    } >> "$AVD_CONFIG"
fi
log "AVD list:"
"$AVDMANAGER" list avd | sed -n '/Name: qa/,/---/p' || true
log "done"
