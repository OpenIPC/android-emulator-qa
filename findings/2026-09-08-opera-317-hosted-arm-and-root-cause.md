# #317: no arm-virt on hosted CI, but the root cause is provable another way

Continuation of `2026-09-06-opera-android-arm-translation-crash.md`. That note
ended "can't reproduce without an arm host." This one records the search for a
hosted arm host — every reachable one is walled — and the root cause that the
reporter's own A/B test already proves without one.

## Every hosted arm path, measured (2026-09-08)

Opera for Android is **arm64-only**, and the modern Android emulator refuses to
run an arm64 image on an x86_64 host:

```
FATAL | Avd's CPU Architecture 'arm64' is not supported by the QEMU2 emulator
        on x86_64 host. System image must match the host architecture.
```

So the emulator must run on a genuine arm64 host with virtualization. None of
the reachable ones provide it:

| host | result |
| --- | --- |
| GitHub `macos-15` (Apple silicon) | emulator dies: `HVF error: HV_UNSUPPORTED` — hosted macOS runners expose no nested virtualization |
| GitHub `ubuntu-24.04-arm` (arm64 Linux) | `/dev/kvm` absent — hosted arm Linux runners expose no KVM |
| this dev box (x86_64, has `/dev/kvm`) | emulator refuses the arm64 image (above); x86 KVM cannot accelerate an arm64 guest |
| cloud arm VM / device farm | no credentials configured (`aws` present but unconfigured; no gcloud/oci) |

GitHub's **x86** Linux runners do have `/dev/kvm`, but an x86 guest can only run
Opera through `libndk_translation`, which crashes (the 2026-09-06 finding).

## The root cause, proved without Opera

The reporter ran an A/B test that is itself the measurement: **PR #396 behaves
identically to `main`** for WebRTC on their Opera. #396 broadened the
gesture-retry to arm on *any* muted rejection, but that arming lives inside
`video.play().catch(...)`. If #396 changed nothing, **`play()` did not reject** —
its `.catch` never ran on either branch.

Yet the picture is paused (no video). So on Opera Android, for a muted
MediaStream, **`video.play()` RESOLVES but playback does not start** until a user
gesture. Both `main` and #396 only arm the gesture-retry on a *rejection*, so
neither ever arms — which is exactly why the reporter sees no change and why a
tap after load does nothing (no handler is armed to call `play()` again).

Chrome control (this box, VA-API, `--autoplay-policy=user-gesture-required`, a
muted `canvas.captureStream()` `<video>`): `play()` resolved **and** the video
played (`playedNoGesture: true`). So `video.paused` after `play()` is the
discriminator — Chrome is not paused (plays), Opera is (per the reporter). A fix
that arms the gesture-retry on the observed *paused* state, not on a `play()`
rejection, triggers on Opera and never on Chrome (no regression).

## What still needs an arm-Opera bench

The fix direction is proven; confirming the *fixed* build on real Opera still
needs an arm host — the cooperative reporter, an arm device farm (creds), or an
arm cloud VM with virt (creds). The autoplay probe (`web/autoplay.js`) + POST
server (`ci/serve.py`) are ready to drive whichever becomes available; the CI
workflow already boots the pieces on any runner that can accelerate.
