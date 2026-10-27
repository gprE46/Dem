import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(ROOT, "addon")
if ADDON_DIR not in sys.path:
    sys.path.insert(0, ADDON_DIR)


def build_multipart(boundary, parts):
    """parts: list of (name, filename_or_None, content_bytes)."""
    out = b""
    for name, filename, content in parts:
        out += b"--" + boundary + b"\r\n"
        disp = 'form-data; name="%s"' % name
        if filename is not None:
            disp += '; filename="%s"' % filename
        out += ("Content-Disposition: " + disp + "\r\n").encode("utf-8")
        if filename is not None:
            out += b"Content-Type: application/octet-stream\r\n"
        out += b"\r\n" + content + b"\r\n"
    out += b"--" + boundary + b"--\r\n"
    return out
