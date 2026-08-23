# android-emulator-qa

A test harness for reproducing **client-side** bug reports against IP cameras by
driving real Android apps (closed-source or your own) on a KVM-accelerated
Android emulator and capturing exactly what they put on the wire.

Built for OpenIPC camera QA — reproduce "no connectivity / auth failed / no
video" reports by clicking through the actual app (e.g. tinyCam Monitor) against
a camera and inspecting the ONVIF/RTSP exchange byte-for-byte.

## Quick start

```bash
make bootstrap                    # once: JDK + Android SDK + emulator + API-30 image (~5 GB)
make boot && make wait            # headless emulator, KVM-accelerated
make install APK=/path/to/app.apk # push an APK
make mvp CAMERA_IP=192.168.1.100  # relay + drive tinyCam ONVIF + print auth analysis
```

## Why a relay instead of `-tcpdump`

The emulator's built-in `-tcpdump` does **not** capture traffic to a camera on
another subnet — the guest reaches it through slirp NAT, so the dump only shows
the guest's own internet traffic. Instead, `harness/relay.py` runs a logging TCP
relay on the host (the emulator reaches the host at `10.0.2.2`); point the app at
the relay and every ONVIF/RTSP byte is logged in both directions, no root needed.

See **[CLAUDE.md](CLAUDE.md)** for the full guide, UI-automation notes, and an
ONVIF/majestic auth cheatsheet. A worked investigation is in
[`findings/`](findings/).

## Layout

| Path | What |
|---|---|
| `bootstrap.sh` | Idempotent JDK + SDK + emulator + AVD setup |
| `harness/relay.py` | Host-side logging TCP relay (the capture tool) |
| `harness/analyze_relay.py` | Summarize a capture: per-request auth mode + response |
| `harness/device.py`, `ui.py` | adb control + uiautomator parsing |
| `harness/camera.py` | SSH helpers for the camera (env-configurable) |
| `flows/tinycam_onvif.py` | Reusable add-ONVIF-camera flow |

Requires KVM (`/dev/kvm`) and is entirely user-space (no root).
