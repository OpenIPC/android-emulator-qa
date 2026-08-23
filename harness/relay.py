#!/usr/bin/env python3
"""Logging TCP relay for capturing guest<->camera protocol traffic without
privileges.

The Android emulator reaches the host at 10.0.2.2 (slirp gateway). Point the
app at 10.0.2.2:<listen> and this relay forwards to the real camera, dumping
every byte in both directions with timestamps + a raw stream per connection.

Usage (replace 192.168.1.100 with your camera's address):
  relay.py --outdir RUN/relay \
           --map 8080:192.168.1.100:80 \
           --map 8554:192.168.1.100:554
"""
import argparse
import os
import selectors
import socket
import threading
import time
from pathlib import Path

_conn_no = 0
_lock = threading.Lock()


def next_id():
    global _conn_no
    with _lock:
        _conn_no += 1
        return _conn_no


def log_line(combined, s):
    with _lock:
        combined.write(s + "\n")
        combined.flush()


def pump(cid, tag, src, dst, outdir, combined, raw):
    """Copy src->dst, logging chunks. tag is 'C>S' or 'S>C'."""
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            raw.write(data)
            raw.flush()
            dst.sendall(data)
            ts = f"{time.time():.3f}"
            # printable preview
            try:
                text = data.decode("latin1")
            except Exception:
                text = repr(data)
            log_line(combined, f"\n--- [{cid}] {tag} {len(data)}B @ {ts} ---")
            log_line(combined, text)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle(client, listen_port, target_host, target_port, outdir, combined):
    cid = next_id()
    peer = client.getpeername()
    log_line(combined, f"\n==== [{cid}] NEW conn on :{listen_port} from {peer} "
                       f"-> {target_host}:{target_port} @ {time.time():.3f} ====")
    try:
        server = socket.create_connection((target_host, target_port), timeout=10)
    except OSError as e:
        log_line(combined, f"[{cid}] upstream connect failed: {e}")
        client.close()
        return
    raw_cs = open(Path(outdir) / f"conn{cid:03d}_C2S_:{listen_port}.raw", "wb")
    raw_sc = open(Path(outdir) / f"conn{cid:03d}_S2C_:{listen_port}.raw", "wb")
    t1 = threading.Thread(target=pump, args=(cid, "C>S", client, server, outdir, combined, raw_cs))
    t2 = threading.Thread(target=pump, args=(cid, "S>C", server, client, outdir, combined, raw_sc))
    t1.start(); t2.start(); t1.join(); t2.join()
    client.close(); server.close()
    raw_cs.close(); raw_sc.close()
    log_line(combined, f"==== [{cid}] closed @ {time.time():.3f} ====")


def listener(listen_port, target_host, target_port, outdir, combined):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", listen_port))
    s.listen(16)
    log_line(combined, f"listening :{listen_port} -> {target_host}:{target_port}")
    while True:
        client, _ = s.accept()
        threading.Thread(target=handle, args=(client, listen_port, target_host,
                                              target_port, outdir, combined),
                         daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--map", action="append", required=True,
                    help="listenPort:targetHost:targetPort")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    combined = open(Path(args.outdir) / "stream.log", "w")
    threads = []
    for m in args.map:
        lp, th, tp = m.split(":")
        t = threading.Thread(target=listener, args=(int(lp), th, int(tp),
                                                    args.outdir, combined), daemon=True)
        t.start()
        threads.append(t)
    print(f"relay up: {args.map} -> {args.outdir}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
