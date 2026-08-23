#!/usr/bin/env python3
"""Minimal SIP UAC that places a P2P call to a majestic camera and captures the
RTP + RTCP the camera sends back, so we can inspect RTP timestamps and RTCP
Sender-Report clock domains directly.

This is a diagnostic probe, not a softphone: it offers audio (PCMA) + video
(H264) to our own host ports, answers/ACKs, receives media for a few seconds,
prints an RTP/RTCP timestamp analysis, then hangs up (BYE).

Why this exists: the majestic SIP media path reuses the RTSP stream machinery
(RtspStream + on_sr_tick SR emitter). A clock-domain mismatch between the RTCP
SR rtp_ts and the RTP packet timestamps makes receivers that drive their jitter
buffer from RTCP SR (Linphone/mediastreamer2) diverge, while receivers that
ignore SR (baresip) play fine. This probe reads the raw numbers to confirm.

Usage:
  python3 harness/sip_probe.py --camera-ip 10.216.128.33 --local-ip 10.216.135.2 \
      --user 222 --duration 12 --outdir runs/<ts>/sip
"""
import argparse
import os
import socket
import struct
import time
import hashlib
from pathlib import Path


def sdp(local_ip, aport, vport):
    return (
        "v=0\r\n"
        f"o=- 0 0 IN IP4 {local_ip}\r\n"
        "s=probe\r\n"
        f"c=IN IP4 {local_ip}\r\n"
        "t=0 0\r\n"
        f"m=audio {aport} RTP/AVP 8\r\n"
        "a=rtpmap:8 PCMA/8000\r\n"
        "a=sendrecv\r\n"
        f"m=video {vport} RTP/AVP 96\r\n"
        "a=rtpmap:96 H264/90000\r\n"
        "a=fmtp:96 packetization-mode=1\r\n"
        "a=sendrecv\r\n"
    )


def digest_auth(user, pw, method, uri, realm, nonce, qop=None, nc="00000001",
                cnonce="0a4f113b"):
    ha1 = hashlib.md5(f"{user}:{realm}:{pw}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    if qop:
        resp = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()).hexdigest()
        return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
                f'uri="{uri}", response="{resp}", qop={qop}, nc={nc}, cnonce="{cnonce}"')
    resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", response="{resp}"')


def parse_auth(headers):
    for line in headers.split("\r\n"):
        if line.lower().startswith(("www-authenticate", "proxy-authenticate")):
            realm = _tok(line, "realm")
            nonce = _tok(line, "nonce")
            qop = _tok(line, "qop")
            return realm, nonce, qop
    return None, None, None


def _tok(line, key):
    import re
    m = re.search(key + r'="?([^",]+)"?', line)
    return m.group(1) if m else None


class RtpStat:
    def __init__(self, name):
        self.name = name
        self.n = 0
        self.first_ts = None
        self.last_ts = None
        self.first_seq = None
        self.last_seq = None
        self.first_arrival = None
        self.last_arrival = None
        self.ssrc = None
        self.pt = None
        self.sr = []  # list of (ntp_sec, ntp_frac, rtp_ts, arrival)

    def on_rtp(self, data, arrival):
        if len(data) < 12:
            return
        b0, b1, seq, ts, ssrc = struct.unpack("!BBHII", data[:12])
        self.pt = b1 & 0x7F
        self.n += 1
        if self.first_ts is None:
            self.first_ts, self.first_seq, self.first_arrival = ts, seq, arrival
            self.ssrc = ssrc
        self.last_ts, self.last_seq, self.last_arrival = ts, seq, arrival

    def on_rtcp(self, data, arrival):
        # parse compound RTCP; look for SR (PT=200)
        off = 0
        while off + 4 <= len(data):
            b0, pt, length = data[off], data[off + 1], struct.unpack("!H", data[off+2:off+4])[0]
            blocklen = (length + 1) * 4
            if pt == 200 and off + 28 <= len(data):
                ssrc, ntp_sec, ntp_frac, rtp_ts, pkts, octs = struct.unpack(
                    "!IIIIII", data[off+4:off+28])
                self.sr.append((ntp_sec, ntp_frac, rtp_ts, arrival, ssrc))
            off += blocklen if blocklen else 4

    def report(self, clock):
        lines = [f"--- {self.name} (PT={self.pt}, ssrc={self.ssrc}) ---"]
        if self.n < 2:
            lines.append(f"  packets: {self.n} (insufficient)")
            return "\n".join(lines)
        ts_span = (self.last_ts - self.first_ts) & 0xFFFFFFFF
        wall = self.last_arrival - self.first_arrival
        implied_hz = ts_span / wall if wall > 0 else 0
        lines.append(f"  packets: {self.n}, seq {self.first_seq}->{self.last_seq}")
        lines.append(f"  first_rtp_ts: {self.first_ts}  last_rtp_ts: {self.last_ts}")
        lines.append(f"  rtp_ts span: {ts_span} over {wall:.3f}s wall")
        lines.append(f"  implied clock: {implied_hz:.1f} Hz (declared {clock} Hz)  "
                     f"ratio={implied_hz/clock:.3f}" if clock else "")
        for (ntp_sec, ntp_frac, rtp_ts, arr, ssrc) in self.sr:
            ntp = ntp_sec + ntp_frac / 2**32
            # NTP epoch 1900 -> unix 1970 offset
            ntp_unix = ntp - 2208988800
            # where does SR.rtp_ts sit relative to the actual RTP stream?
            delta_from_first = (rtp_ts - self.first_ts) & 0xFFFFFFFF
            # signed interpretation
            if delta_from_first > 2**31:
                delta_from_first -= 2**32
            lines.append(
                f"  RTCP SR: ntp={ntp_unix:.3f} (unix)  rtp_ts={rtp_ts}  "
                f"SR.rtp_ts - first_rtp_ts = {delta_from_first} ticks "
                f"({delta_from_first/clock:.3f}s @ {clock}Hz)" if clock else "")
        if not self.sr:
            lines.append("  RTCP SR: none received")
        return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera-ip", required=True)
    ap.add_argument("--camera-port", type=int, default=5060)
    ap.add_argument("--local-ip", required=True)
    ap.add_argument("--user", default="222")
    ap.add_argument("--password", default="1234")
    ap.add_argument("--duration", type=float, default=12)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sip.bind(("0.0.0.0", 0))
    sip.settimeout(2)
    lport = sip.getsockname()[1]

    # media sockets
    aud = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); aud.bind(("0.0.0.0", 0))
    aport = aud.getsockname()[1]
    audc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); audc.bind(("0.0.0.0", aport + 1))
    vid = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); vid.bind(("0.0.0.0", 0))
    vport = vid.getsockname()[1]
    vidc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); vidc.bind(("0.0.0.0", vport + 1))
    for s in (aud, audc, vid, vidc):
        s.setblocking(False)

    callid = f"probe-{lport}-{int(args.duration)}@{args.local_ip}"
    fromtag = f"tag{lport}"
    target = (args.camera_ip, args.camera_port)
    uri = f"sip:{args.user}@{args.camera_ip}"
    body = sdp(args.local_ip, aport, vport)

    def invite(cseq, auth=None):
        h = [
            f"INVITE {uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {args.local_ip}:{lport};branch=z9hG4bK{cseq}{lport}",
            f"Max-Forwards: 70",
            f"From: <sip:probe@{args.local_ip}>;tag={fromtag}",
            f"To: <{uri}>",
            f"Call-ID: {callid}",
            f"CSeq: {cseq} INVITE",
            f"Contact: <sip:probe@{args.local_ip}:{lport}>",
        ]
        if auth:
            h.append(f"Authorization: {auth}")
        h += [
            "Content-Type: application/sdp",
            f"Content-Length: {len(body)}",
            "", body,
        ]
        return "\r\n".join(h).encode()

    log = open(Path(args.outdir) / "sip.log", "w")
    def L(*a):
        msg = " ".join(str(x) for x in a)
        print(msg); log.write(msg + "\n"); log.flush()

    L(f"[probe] local {args.local_ip}:{lport} audio={aport} video={vport} -> {target}")
    cseq = 1
    sip.sendto(invite(cseq), target)
    ok_headers = None
    remote_tag = None
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            data, _ = sip.recvfrom(65535)
        except socket.timeout:
            sip.sendto(invite(cseq), target); continue
        text = data.decode("latin1")
        status = text.split("\r\n", 1)[0]
        L("[sip <]", status)
        if status.startswith("SIP/2.0 100") or status.startswith("SIP/2.0 180"):
            continue
        if status.startswith("SIP/2.0 401") or status.startswith("SIP/2.0 407"):
            realm, nonce, qop = parse_auth(text)
            cseq += 1
            a = digest_auth(args.user, args.password, "INVITE", uri, realm, nonce, qop)
            sip.sendto(invite(cseq, a), target)
            continue
        if status.startswith("SIP/2.0 2"):
            ok_headers = text
            for line in text.split("\r\n"):
                if line.lower().startswith("to:") and "tag=" in line:
                    remote_tag = line.split("tag=", 1)[1].strip()
            break
        L("[sip] non-2xx final:", status); break

    if not ok_headers:
        L("[probe] no 200 OK — aborting"); return

    # ACK
    ack = "\r\n".join([
        f"ACK {uri} SIP/2.0",
        f"Via: SIP/2.0/UDP {args.local_ip}:{lport};branch=z9hG4bKack{lport}",
        "Max-Forwards: 70",
        f"From: <sip:probe@{args.local_ip}>;tag={fromtag}",
        f"To: <{uri}>" + (f";tag={remote_tag}" if remote_tag else ""),
        f"Call-ID: {callid}",
        f"CSeq: {cseq} ACK",
        "Content-Length: 0", "", "",
    ]).encode()
    sip.sendto(ack, target)
    L("[probe] ACK sent, capturing media for", args.duration, "s")

    astat, vstat = RtpStat("AUDIO"), RtpStat("VIDEO")
    araw = open(Path(args.outdir) / "audio_rtp.bin", "wb")
    end = time.time() + args.duration
    while time.time() < end:
        for s, kind in ((aud, "a-rtp"), (audc, "a-rtcp"), (vid, "v-rtp"), (vidc, "v-rtcp")):
            try:
                while True:
                    d, _ = s.recvfrom(65535)
                    now = time.time()
                    if kind == "a-rtp":
                        astat.on_rtp(d, now); araw.write(struct.pack("!dI", now, len(d))); araw.write(d)
                    elif kind == "a-rtcp":
                        astat.on_rtcp(d, now)
                    elif kind == "v-rtp":
                        vstat.on_rtp(d, now)
                    elif kind == "v-rtcp":
                        vstat.on_rtcp(d, now)
            except (BlockingIOError, socket.error):
                pass
        time.sleep(0.005)

    # BYE
    cseq += 1
    bye = "\r\n".join([
        f"BYE {uri} SIP/2.0",
        f"Via: SIP/2.0/UDP {args.local_ip}:{lport};branch=z9hG4bKbye{lport}",
        "Max-Forwards: 70",
        f"From: <sip:probe@{args.local_ip}>;tag={fromtag}",
        f"To: <{uri}>" + (f";tag={remote_tag}" if remote_tag else ""),
        f"Call-ID: {callid}",
        f"CSeq: {cseq} BYE",
        "Content-Length: 0", "", "",
    ]).encode()
    sip.sendto(bye, target)
    L("[probe] BYE sent")
    L("")
    L(astat.report(8000))
    L("")
    L(vstat.report(90000))


if __name__ == "__main__":
    main()
