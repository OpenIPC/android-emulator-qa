#!/usr/bin/env python3
"""Summarize a relay capture dir: classify each ONVIF request's auth mode and
majestic's response, so you can see at a glance what the app sent and how the
camera answered.

Usage: python3 harness/analyze_relay.py runs/<ts>/relay
"""
import re
import sys
from pathlib import Path


def classify_request(raw: bytes):
    text = raw.decode("latin1", "replace")
    op = None
    m = re.search(r'action="[^"]*/(Get[A-Za-z]+|[A-Za-z]+)"', text)
    if m:
        op = m.group(1)
    if not op:
        m = re.search(r"<[\w:]*Body[^>]*>\s*<[\w:]*([A-Za-z]+)", text)
        op = m.group(1) if m else "(none)"
    auth = "(no-token)"
    if "PasswordDigest" in text:
        auth = "PasswordDigest"
    elif "PasswordText" in text:
        auth = "PasswordText"
    elif re.search(r"Authorization:\s*Basic", text, re.I):
        auth = "HTTP-Basic"
    elif re.search(r"Authorization:\s*Digest", text, re.I):
        auth = "HTTP-Digest"
    # prefixed vs default-namespace WSSE (the tinyCam root cause discriminator)
    ns = ""
    if "UsernameToken" in text:
        ns = "prefixed" if re.search(r"<\w+:Security", text) else "unprefixed"
    verb = text.split(" ", 1)[0]
    return verb, op, auth, ns


def classify_response(raw: bytes):
    text = raw.decode("latin1", "replace")
    line = text.split("\r\n", 1)[0]
    if "Manufacturer" in text:
        return line, "OK (auth accepted)"
    if "FailedAuthentication" in text:
        return line, "WSSE FailedAuthentication"
    if "401" in line:
        wa = re.search(r"WWW-Authenticate:\s*(\S+)", text)
        return line, f"HTTP challenge ({wa.group(1) if wa else '?'})"
    return line, ""


def main():
    d = Path(sys.argv[1])
    reqs = sorted(d.glob("conn*_C2S_*.raw"))
    print(f"=== {d}: {len(reqs)} client->camera messages ===")
    seen = {}
    for req in reqs:
        cid = req.name.split("_")[0]
        resp = next(iter(d.glob(f"{cid}_S2C_*.raw")), None)
        verb, op, auth, ns = classify_request(req.read_bytes())
        rline, rverdict = classify_response(resp.read_bytes()) if resp else ("", "")
        key = (verb, op, auth, ns, rverdict)
        seen[key] = seen.get(key, 0) + 1
    for (verb, op, auth, ns, rverdict), count in sorted(seen.items(),
                                                        key=lambda x: -x[1]):
        nslbl = f" ns={ns}" if ns else ""
        print(f"  x{count:<3} {verb:5} {op:22} auth={auth:14}{nslbl:16} -> {rverdict}")


if __name__ == "__main__":
    main()
