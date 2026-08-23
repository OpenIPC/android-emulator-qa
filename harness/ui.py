"""Parse uiautomator dumps and locate widgets."""
import re
import xml.etree.ElementTree as ET

BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class Node:
    def __init__(self, el):
        self.el = el
        self.text = el.get("text", "")
        self.desc = el.get("content-desc", "")
        self.rid = el.get("resource-id", "")
        self.cls = el.get("class", "")
        self.clickable = el.get("clickable") == "true"
        m = BOUNDS_RE.match(el.get("bounds", "[0,0][0,0]"))
        self.x1, self.y1, self.x2, self.y2 = map(int, m.groups()) if m else (0, 0, 0, 0)

    @property
    def center(self):
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    def __repr__(self):
        return (f"<Node cls={self.cls.split('.')[-1]} rid={self.rid.split('/')[-1]!r} "
                f"text={self.text!r} desc={self.desc!r} bounds=({self.x1},{self.y1},{self.x2},{self.y2})>")


class Screen:
    def __init__(self, xml_str):
        self.root = ET.fromstring(xml_str)
        self.nodes = [Node(el) for el in self.root.iter("node")]

    def find(self, text=None, rid=None, desc=None, cls=None, contains=None, clickable=None):
        out = []
        for n in self.nodes:
            if text is not None and n.text != text:
                continue
            if contains is not None and contains.lower() not in (n.text + " " + n.desc).lower():
                continue
            if rid is not None and not n.rid.endswith(rid):
                continue
            if desc is not None and n.desc != desc:
                continue
            if cls is not None and not n.cls.endswith(cls):
                continue
            if clickable is not None and n.clickable != clickable:
                continue
            out.append(n)
        return out

    def first(self, **kw):
        r = self.find(**kw)
        return r[0] if r else None

    def texts(self):
        return [n for n in self.nodes if n.text or n.desc]
