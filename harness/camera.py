"""ssh helpers for the lab camera (key-based, non-interactive).

Camera coordinates come from the environment so nothing site-specific is
hard-coded: set CAMERA_HOST / CAMERA_IP / CAMERA_USER / CAMERA_PASS to point at
your own camera. Defaults are generic placeholders.
"""
import os
import subprocess

CAMERA_HOST = os.environ.get("CAMERA_HOST", "openipc-camera.local")
CAMERA_IP = os.environ.get("CAMERA_IP", "192.168.1.100")
CAMERA_USER = os.environ.get("CAMERA_USER", "root")
CAMERA_WEB_PASS = os.environ.get("CAMERA_PASS", "123456")  # OpenIPC published default

SSH_BASE = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            f"{CAMERA_USER}@{CAMERA_HOST}"]


def run(cmd, timeout=30):
    """Run a shell command on the camera, return stdout (raises on failure)."""
    res = subprocess.run(SSH_BASE + [cmd], capture_output=True, text=True,
                         timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"camera ssh failed rc={res.returncode}: {res.stderr[:500]}")
    return res.stdout


def majestic_version():
    return run("majestic --version 2>&1 | head -1").strip()

def onvif_config():
    return run('sed -n "/^onvif:/,/^[a-z]/p" /etc/majestic.yaml')

def logread_since_marker(marker):
    """Return syslog lines after the last occurrence of marker (see log_marker)."""
    return run(f"logread | sed -n '/{marker}/,$p'", timeout=60)

def log_marker(tag):
    run(f"logger MARKER-{tag}")
    return f"MARKER-{tag}"
