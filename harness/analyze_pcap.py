#!/usr/bin/env python3
"""Parse an emulator -tcpdump pcap and reconstruct TCP streams to/from the
camera, then classify ONVIF (HTTP/SOAP) and RTSP exchanges and their auth.

Pure stdlib: reads classic pcap (little/big endian), reassembles TCP payloads
per (src,sport,dst,dport) in capture order (good enough for these short,
in-order lab flows), and slices out HTTP + RTSP request/response messages.
"""
import argparse
import base64
import json
import re
import struct
import sys
from collections import defaultdict


def read_pcap(path):
    with open(path, "rb") as f:
        data = f.read()
    magic = data[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4"):
        le = magic == b"\xd4\xc3\xb2\xa1"
        endian = "<" if le else ">"
    else:
        raise ValueError(f"not a classic pcap (magic {magic!r}); pcapng unsupported")
    # global header 24 bytes; snaplen at 16, linktype at 20
    linktype = struct.unpack_from(endian + "I", data, 20)[0]
    off = 24
    pkts = []
    while off + 16 <= len(data):
        ts_sec, ts_usec, incl, orig = struct.unpack_from(endian + "IIII", data, off)
        off += 16
        if off + incl > len(data):
            break
        pkts.append((ts_sec + ts_usec / 1e6, data[off:off + incl]))
        off += incl
    return linktype, pkts


def parse_eth_ipv4_tcp(frame, linktype):
    # linktype 1 = Ethernet, 101 = raw IP
    if linktype == 1:
        if len(frame) < 14:
            return None
        eth_type = struct.unpack_from(">H", frame, 12)[0]
        if eth_type != 0x0800:
            return None
        ip = frame[14:]
    elif linktype == 101:
        ip = frame
    else:
        # try ethernet anyway
        ip = frame[14:] if len(frame) > 14 else frame
    if len(ip) < 20 or (ip[0] >> 4) != 4:
        return None
    ihl = (ip[0] & 0x0F) * 4
    proto = ip[9]
    if proto != 6:  # TCP
        return None
    src = ".".join(str(b) for b in ip[12:16])
    dst = ".".join(str(b) for b in ip[16:20])
    total_len = struct.unpack_from(">H", ip, 2)[0]
    tcp = ip[ihl:total_len] if total_len else ip[ihl:]
    if len(tcp) < 20:
        return None
    sport, dport, seq, ack = struct.unpack_from(">HHII", tcp, 0)
    data_off = (tcp[12] >> 4) * 4
    flags = tcp[13]
    payload = tcp[data_off:]
    return dict(src=src, dst=dst, sport=sport, dport=dport, seq=seq,
                flags=flags, payload=payload)


def reassemble(pkts, linktype):
    """Return dict keyed by directional flow -> concatenated bytes, plus the
    set of TCP endpoints seen."""
    flows = defaultdict(bytearray)
    endpoints = set()
    for ts, frame in pkts:
        seg = parse_eth_ipv4_tcp(frame, linktype)
        if not seg or not seg["payload"]:
            continue
        key = (seg["src"], seg["sport"], seg["dst"], seg["dport"])
        flows[key] += seg["payload"]
        endpoints.add((seg["src"], seg["sport"], seg["dst"], seg["dport"]))
    return flows


HTTP_REQ = re.compile(rb"^(GET|POST|PUT|HEAD|OPTIONS|DELETE) ", re.M)
RTSP_REQ = re.compile(rb"^(OPTIONS|DESCRIBE|SETUP|PLAY|TEARDOWN|GET_PARAMETER|PAUSE) ", re.M)
MSG_SPLIT = re.compile(rb"(?=^(?:GET|POST|PUT|HEAD|OPTIONS|DELETE|DESCRIBE|SETUP|PLAY|TEARDOWN|GET_PARAMETER|PAUSE|RTSP/1\.0|HTTP/1\.[01]) )", re.M)


def split_messages(blob):
    parts = [p for p in MSG_SPLIT.split(blob) if p.strip()]
    return parts


def classify_soap_auth(body):
    b = body.decode("latin1", "replace")
    tags = []
    if "PasswordDigest" in b:
        tags.append("WSSE:PasswordDigest")
    if "PasswordText" in b:
        tags.append("WSSE:PasswordText")
    if "UsernameToken" in b and not tags:
        tags.append("WSSE:UsernameToken(unknown)")
    m = re.search(r"<[\w:]*Body[^>]*>\s*<[\w:]*([A-Za-z]+)", b)
    op = m.group(1) if m else None
    return op, tags


def summarize(blob, kind):
    """Yield dicts describing each request/response message in a flow blob."""
    out = []
    for msg in split_messages(blob):
        head, _, body = msg.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        if not lines:
            continue
        start = lines[0].decode("latin1", "replace")
        headers = {}
        for ln in lines[1:]:
            if b":" in ln:
                k, _, v = ln.partition(b":")
                headers[k.decode("latin1").strip().lower()] = v.decode("latin1").strip()
        entry = {"start": start[:120]}
        auth = headers.get("authorization")
        if auth:
            scheme = auth.split(" ", 1)[0]
            entry["http_auth"] = scheme
            if scheme.lower() == "basic":
                try:
                    entry["http_auth_decoded"] = base64.b64decode(
                        auth.split(" ", 1)[1]).decode("latin1")
                except Exception:
                    pass
        wa = headers.get("www-authenticate")
        if wa:
            entry["www_authenticate"] = wa[:120]
        if headers.get("content-type"):
            entry["content_type"] = headers["content-type"][:60]
        # SOAP op + wsse
        if b"Envelope" in body or "soap" in headers.get("content-type", "").lower() \
                or "xml" in headers.get("content-type", "").lower():
            op, tags = classify_soap_auth(body)
            if op:
                entry["soap_op"] = op
            if tags:
                entry["soap_auth"] = tags
        # SOAP fault / onvif error
        if b"Fault" in body or b"FailedAuthentication" in body:
            fm = re.search(rb"<[\w:]*(?:Text|faultstring|Reason)[^>]*>([^<]+)<", body)
            entry["fault"] = (fm.group(1).decode("latin1")[:120] if fm else "fault")
        out.append(entry)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap")
    ap.add_argument("--camera-ip", default="192.168.1.100")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    linktype, pkts = read_pcap(args.pcap)
    flows = reassemble(pkts, linktype)

    report = {"pcap": args.pcap, "packets": len(pkts), "camera_ip": args.camera_ip,
              "flows": []}
    for (src, sport, dst, dport), blob in flows.items():
        involves_cam = args.camera_ip in (src, dst)
        if not involves_cam:
            continue
        is_http = bool(HTTP_REQ.search(blob)) or blob[:5] == b"HTTP/"
        is_rtsp = bool(RTSP_REQ.search(blob)) or b"RTSP/1.0" in blob[:200]
        kind = "rtsp" if is_rtsp else ("http" if is_http else "other")
        direction = "client->cam" if dst == args.camera_ip else "cam->client"
        f = {"src": f"{src}:{sport}", "dst": f"{dst}:{dport}",
             "direction": direction, "kind": kind, "bytes": len(blob)}
        if kind in ("http", "rtsp"):
            f["messages"] = summarize(blob, kind)
        report["flows"].append(f)

    # port summary
    ports = defaultdict(int)
    for (src, sport, dst, dport), blob in flows.items():
        if dst == args.camera_ip:
            ports[dport] += len(blob)
    report["camera_ports_contacted"] = dict(sorted(ports.items()))

    if args.json:
        print(json.dumps(report, indent=2))
        return

    print(f"=== {args.pcap}: {len(pkts)} pkts, camera {args.camera_ip} ===")
    print(f"camera ports contacted (dport: bytes): {report['camera_ports_contacted']}")
    for f in report["flows"]:
        print(f"\n[{f['kind'].upper()}] {f['direction']}  {f['src']} -> {f['dst']}  ({f['bytes']}B)")
        for m in f.get("messages", []):
            extra = {k: v for k, v in m.items() if k != "start"}
            print(f"    {m['start']}")
            if extra:
                print(f"        {extra}")


if __name__ == "__main__":
    main()
