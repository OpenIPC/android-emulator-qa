"""Step runner: every UI action archives a screenshot + uiautomator dump."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from device import Device          # noqa: E402
from ui import Screen              # noqa: E402


class StepError(RuntimeError):
    pass


class Runner:
    def __init__(self, run_dir, serial="emulator-5554"):
        self.dev = Device(serial)
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.n = 0
        self.journal = []

    def _archive(self, label):
        self.n += 1
        base = self.run_dir / f"{self.n:03d}_{label}"
        self.dev.screencap(f"{base}.png")
        xml = None
        try:
            xml = self.dev.ui_dump()
            Path(f"{base}.xml").write_text(xml)
        except Exception as e:
            Path(f"{base}.xml.err").write_text(str(e))
        self.journal.append({"step": self.n, "label": label, "t": time.time()})
        self._save_journal()
        return Screen(xml) if xml else None

    def _save_journal(self):
        (self.run_dir / "journal.json").write_text(json.dumps(self.journal, indent=1))

    def snap(self, label="snap"):
        """Screenshot + dump current screen, return parsed Screen."""
        return self._archive(label)

    def tap_text(self, *candidates, label=None, settle=1.5, required=True):
        """Find first node whose text/desc contains any candidate; tap it."""
        scr = self._archive(label or f"before_tap_{candidates[0][:20]}")
        for cand in candidates:
            node = scr.first(contains=cand)
            if node:
                # prefer a clickable ancestor position: just tap center
                self.dev.tap(*node.center)
                time.sleep(settle)
                return node
        if required:
            visible = [f"{n.text or n.desc}" for n in scr.texts()][:40]
            raise StepError(f"none of {candidates} found; visible: {visible}")
        return None

    def tap_node(self, node, settle=1.5):
        self.dev.tap(*node.center)
        time.sleep(settle)

    def type_text(self, s, settle=0.5):
        self.dev.text(s)
        time.sleep(settle)

    def wait_for(self, *candidates, timeout=30, interval=2, label="wait"):
        """Poll until any candidate text appears; return (matched_text, Screen)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            scr = self._archive(label)
            for cand in candidates:
                if scr and scr.first(contains=cand):
                    return cand, scr
            time.sleep(interval)
        return None, scr
