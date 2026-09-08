// Measure Opera-for-Android autoplay of a muted MediaStream <video> — the exact
// setup preview-webrtc.js's ontrack creates (srcObject = remote stream, muted,
// play()). #317: the picture is ready but paused and never starts on its own.
//
// The key questions this answers on REAL Opera:
//   - does muted autoplay start without a gesture?         (playedNoGesture)
//   - does the explicit play() reject, and with what name? (playPromiseA)
//     — because #396 only armed a gesture-retry on a REJECTION; if Opera
//       resolves play() without playing, no retry ever arms, which is why the
//       reporter saw #396 behave exactly like main.
//   - does a real user gesture then start it?              (playedAfterGesture)
//
// Verdict is POSTed to /result (twice: an intermediate "ready for the tap", then
// final) so the CI needs no DevTools bridge.
(function () {
  'use strict';
  var R = {
    ua: navigator.userAgent, phase: 'A',
    playPromiseA: null, playPromiseB: null,
    playedNoGesture: false, playedAfterGesture: false, gestureSeen: false,
    currentTimeA: 0, currentTimeB: 0, readyForGesture: false,
    samples: [], reproduced: null, done: false
  };
  window.__progress = R;  // live; window.__result is set only at finish

  function post(done) {
    R.done = !!done;
    var body = JSON.stringify(R);
    try {
      if (navigator.sendBeacon) navigator.sendBeacon('/result', body);
      else fetch('/result', { method: 'POST', body: body, keepalive: true });
    } catch (e) {}
    if (done) { window.__result = R; document.title = 'DONE'; }
    try { document.getElementById('log').textContent = body; } catch (e) {}
  }

  var video = document.getElementById('v');
  var canvas = document.getElementById('c');
  var ctx = canvas.getContext('2d');
  canvas.width = 320; canvas.height = 180;
  function draw() {
    var x = (Date.now() / 16) % 260;
    ctx.fillStyle = '#123'; ctx.fillRect(0, 0, 320, 180);
    ctx.fillStyle = '#0f0'; ctx.fillRect(x, 60, 60, 60);
    requestAnimationFrame(draw);
  }
  draw();

  var stream = canvas.captureStream(15);
  video.muted = true;
  video.playsInline = true;
  video.srcObject = stream;

  video.addEventListener('playing', function () {
    if (R.phase === 'A') R.playedNoGesture = true; else R.playedAfterGesture = true;
  });

  // Explicit play(), capturing whether the promise resolves or rejects (name).
  var p = video.play();
  if (p && p.then) p.then(
    function () { R.playPromiseA = 'resolved'; },
    function (e) { R.playPromiseA = 'rejected:' + (e && e.name); });
  else R.playPromiseA = 'no-promise';

  // The gesture the fix relies on.
  function onGesture() {
    if (R.phase === 'B') return;
    R.gestureSeen = true; R.phase = 'B';
    var p2 = video.play();
    if (p2 && p2.then) p2.then(
      function () { R.playPromiseB = 'resolved'; },
      function (e) { R.playPromiseB = 'rejected:' + (e && e.name); });
  }
  ['pointerdown', 'touchstart', 'click', 'keydown'].forEach(function (ev) {
    document.addEventListener(ev, onGesture, true);
  });

  var n = 0, t0 = Date.now();
  var iv = setInterval(function () {
    n++;
    var s = { t: +((Date.now() - t0) / 1000).toFixed(1), paused: video.paused,
              ct: +(video.currentTime || 0).toFixed(2), phase: R.phase };
    if (R.samples.length < 120) R.samples.push(s);
    if (R.phase === 'A') R.currentTimeA = video.currentTime; else R.currentTimeB = video.currentTime;

    if (n === 40 && !R.readyForGesture) { R.readyForGesture = true; post(false); }  // ~4s: signal CI to tap
    if (n >= 100) {                                                                  // ~10s total
      clearInterval(iv);
      // #317 reproduced iff muted autoplay never started on its own.
      R.reproduced = !R.playedNoGesture;
      post(true);
    }
  }, 100);
})();
