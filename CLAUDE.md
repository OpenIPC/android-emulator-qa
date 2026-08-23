# Android Emulator QA Harness

A test harness for reproducing **client-side** bug reports against OpenIPC lab
cameras by driving real Android apps (closed-source or our own) on an emulator
and capturing exactly what they put on the wire.

Two purposes:
1. Debug closed-source APKs (tinyCam, ODM, …) that exercise ONVIF/RTSP/etc.
   against cameras — reproduce "no connectivity / auth failed / no video"
   reports by clicking through the real app and capturing the traffic.
2. E2e-test our own upcoming Android software (WebRTC libraries bundled in
   Android). *Not built yet — the device/UI/capture layers are app-agnostic, so
   it's a new `flows/` entry, not a rewrite.*

## Layout

```
bootstrap.sh          Idempotent: fetch JDK 17 + Android SDK + emulator + API-30
                      x86_64 image, create AVD "qa". Everything lands under sdk/.
Makefile              make bootstrap / boot / wait / install / mvp / analyze
harness/
  device.py           adb wrapper: boot wait, install, screencap, uiautomator
                      dump, input tap/text/swipe/key, logcat
  ui.py               Parse a uiautomator dump -> find nodes by text/resource-id
  capture.py          Emulator lifecycle (start/stop, optional -tcpdump)
  relay.py            *** Host-side logging TCP relay — the real capture tool ***
  analyze_relay.py    Summarize a relay dir: per-request auth mode + response
  analyze_pcap.py     Parse a classic pcap (stdlib only) — for -tcpdump captures
  sip_probe.py        Minimal SIP UAC: P2P-call a majestic camera, capture the
                      RTP/RTCP it sends, analyze RTP timestamps + RTCP SR
                      (used to diagnose the Linphone SIP-drop, majestic#398)
  camera.py           SSH helpers for the lab camera (version, config, logs)
  runner.py           Step runner that archives screenshot+dump each action
flows/
  tinycam_onvif.py    Add-ONVIF-camera flow, parameterized, drives by id/text
runs/<ts>-.../        Per-run artifacts (gitignored): screenshots, relay dumps,
                      verdict.json
findings/             Committed investigation writeups (small; no big binaries)
```

`sdk/` (~5 GB) and `runs/` are gitignored. Re-create `sdk/` with `make bootstrap`.

## Requirements

- **KVM** (`/dev/kvm`) for a full-speed emulator. Verify with a real
  `KVM_CREATE_VM` ioctl, not just the device node's permission bits.
- Everything is **user-space — no root needed**. If running in a sandbox where
  `sudo` is unavailable, that's fine; nothing here requires it.
- A reachable camera. Point the harness at yours via `CAMERA_HOST` / `CAMERA_IP`
  env vars (see `harness/camera.py`) or `make CAMERA_IP=…`. SSH key auth to the
  camera is optional but lets `harness/camera.py` read majestic version/config.
  OpenIPC's published default web creds are `root` / `123456`.
- `make bootstrap` needs internet (dl.google.com, adoptium).

## Quick start

```bash
make bootstrap          # once (~5 GB download)
make boot && make wait  # headless emulator, KVM
make install            # push the tinyCam APK ($HOME/tinyCam-...apk)
make mvp                # relay + drive tinyCam ONVIF + print auth analysis
```

`make mvp` starts the relay, runs `flows/tinycam_onvif.py` pointed at the relay,
then prints what auth mode the app sent and how the camera answered.

## THE key gotcha: capturing camera traffic

**The emulator's `-tcpdump` does NOT capture traffic to the lab camera.** The
guest reaches the camera's subnet through slirp NAT via the host; `-tcpdump`
only records the guest's own internet traffic (you'll see Google/GMS chatter,
never the camera IP). Confirmed by byte-grepping the pcap for the camera IP:
zero hits.

**Use the host-side logging relay instead** (`harness/relay.py`):

- The emulator reaches the host at **`10.0.2.2`** (slirp gateway = host loopback).
- Run the relay on the host: it listens on host ports, forwards to the camera,
  and dumps every byte both directions (`conn###_C2S_*.raw` / `_S2C_*.raw` +
  `stream.log`).
- Point the app at `10.0.2.2:<relayport>`. Now all ONVIF/RTSP flows through a
  logger you control — cleartext, both directions, zero privilege.

```bash
python3 harness/relay.py --outdir runs/x/relay \
  --map 8080:$CAMERA_IP:80 --map 8554:$CAMERA_IP:554   # $CAMERA_IP = your camera
# then set tinyCam host=10.0.2.2, ONVIF port=8080, RTSP port=8554
python3 harness/analyze_relay.py runs/x/relay
```

For traffic that *does* stay guest-local you can still use `-tcpdump` +
`harness/analyze_pcap.py`, but for camera work the relay is the reliable path.

## Driving the UI

tinyCam v18 has no automation hooks, so drive it via `uiautomator dump` +
`input`. Rules that keep it robust:

- **Find by resource-id or visible text, derive tap coords from node bounds** —
  never hard-code pixels. `harness/ui.py` (`Screen.first(rid=…, contains=…)`)
  and `flows/tinycam_onvif.py` show the pattern.
- **Archive a screenshot + dump before/after every action.** Failures are then
  diagnosable purely from `runs/<ts>/` without re-running.
- tinyCam text-input dialogs: fields may be prefilled — `MOVE_END` then DEL a
  bunch before typing. A "port" dialog has an **Auto** checkbox that disables
  the field; uncheck it first.
- Read `Read`-able screenshots to see state; the model can inspect the PNGs.

Useful tinyCam resource-ids: `fab_main`, `fab_add_ip_cam`, `fab_add_android_cam`,
`fab_scan`, `action_more`. Package: `com.alexvas.dvr.pro`. Brand chooser has a
search; "(ONVIF) Profile S" is the generic ONVIF model.

## Camera side

`harness/camera.py` wraps SSH to the lab camera. Handy majestic bits:

- `cli -g .onvif.password` / `cli -s .onvif.password <pw>` then
  `killall -HUP majestic` (hot-reloads config, no restart).
- `majestic --version` prints `master+<sha>, <date>`.
- The camera is buildroot/busybox — **no tcpdump on the camera**; use the relay.
- **Always restore camera state you change** (e.g. revert `onvif.password` to
  what you found) so the lab stays clean for the next person.

## ONVIF / majestic auth cheatsheet (learned the hard way)

When an ONVIF client "auth fails," capture the WSSE token via the relay and
check these, which are distinct failure modes:

- **Namespace prefixing.** majestic's WSSE parser (`src/onvif/wsse.c`
  `find_username_token`) historically matched only *prefixed*
  `<wsse:Security>`/`<wsse:UsernameToken>`. A client using the **default
  namespace** (`<Security xmlns="…">` with unprefixed children) is rejected even
  with correct credentials. tinyCam sends the unprefixed form — this was the
  root cause of the tinyCam report (widgetii/majestic#397). `analyze_relay.py`
  labels each token `ns=prefixed|unprefixed`.
- **PasswordDigest needs cleartext.** `PasswordDigest` = Base64(SHA1(nonce +
  created + password)) can't be verified against hashed `/etc/shadow`; it only
  works when `onvif.password` (cleartext) is set. `PasswordText` and HTTP Basic
  verify against shadow and work without it.
- **PRE_AUTH ops.** GetSystemDateAndTime, GetCapabilities, GetServices,
  GetServiceCapabilities, GetWsdlUrl, GetHostname must answer anonymously
  (added by PR #357 / `9015d295`). Builds before 2026-08-20 gate everything.
- The auth failure surfaces to the client as HTTP `400` + SOAP
  `wsse:FailedAuthentication`, or (when `onvif.password` is set) a `401` with a
  `WWW-Authenticate: Digest` challenge.

A worked example of this investigation is in `findings/2026-08-23-tinycam-wsse.md`
(root cause behind widgetii/majestic#397).
