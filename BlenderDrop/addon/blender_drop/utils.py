"""Pure-Python helpers (no bpy): file names, extensions, network addresses.

Everything in this module can be imported and unit-tested without Blender.
"""

import os
import re
import socket
import unicodedata

# --------------------------------------------------------------------------
# File types
# --------------------------------------------------------------------------

MODEL_EXTENSIONS = {
    ".blend", ".fbx", ".obj", ".glb", ".gltf", ".abc",
    ".usd", ".usda", ".usdc", ".usdz",
    ".stl", ".ply", ".dae", ".x3d", ".wrl", ".bvh", ".vdb",
}
VECTOR_EXTENSIONS = {".svg"}
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".hdr",
    ".bmp", ".tga", ".webp",
}
ARCHIVE_EXTENSIONS = {".zip"}

ALL_EXTENSIONS = MODEL_EXTENSIONS | VECTOR_EXTENSIONS | IMAGE_EXTENSIONS | ARCHIVE_EXTENSIONS

KIND_MODEL = "model"
KIND_VECTOR = "vector"
KIND_IMAGE = "image"
KIND_ARCHIVE = "archive"


def get_extension(filename):
    """Lower-case extension including the dot ('' when there is none)."""
    return os.path.splitext(filename)[1].lower()


def file_kind(filename):
    ext = get_extension(filename)
    if ext in MODEL_EXTENSIONS:
        return KIND_MODEL
    if ext in VECTOR_EXTENSIONS:
        return KIND_VECTOR
    if ext in IMAGE_EXTENSIONS:
        return KIND_IMAGE
    if ext in ARCHIVE_EXTENSIONS:
        return KIND_ARCHIVE
    return None


# --------------------------------------------------------------------------
# File name sanitising
# --------------------------------------------------------------------------

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *("COM%d" % i for i in range(1, 10)),
    *("LPT%d" % i for i in range(1, 10)),
}
_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
MAX_NAME_BYTES = 180


def _truncate_utf8(text, max_bytes):
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    return data[:max_bytes].decode("utf-8", "ignore")


def sanitize_filename(name, max_bytes=MAX_NAME_BYTES):
    """Turn an untrusted client file name into a safe single path component.

    Keeps spaces, accents and any Unicode letters; strips directories,
    control characters and characters that are invalid on Windows/macOS/Linux.
    The result is never empty, never '.'/'..' and never a reserved device name.
    """
    if not isinstance(name, str):
        name = str(name or "")
    # Drop any directory part sent by the client (both separator styles).
    name = name.replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFC", name)
    # Remove other invisible control/format characters (e.g. RTL overrides).
    name = "".join(
        ch for ch in name
        if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn")
    )
    name = _FORBIDDEN_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")

    stem, ext = os.path.splitext(name)
    if not stem and ext:  # e.g. ".glb"
        stem, ext = ext, ""
        stem = stem.strip(".") or "file"
    stem = stem.strip(" .") or "file"
    ext = re.sub(r"[^A-Za-z0-9.]", "", ext)[:16]

    if stem.split(".")[0].upper() in _WINDOWS_RESERVED:
        stem = "_" + stem

    stem = _truncate_utf8(stem, max(1, max_bytes - len(ext.encode("utf-8")))).rstrip(" .") or "file"
    return stem + ext


def display_stem(filename):
    """Name without extension, used for collections."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    return stem or "Imported"


def human_size(num_bytes):
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%d %s" % (size, unit)) if unit == "B" else ("%.1f %s" % (size, unit))
        size /= 1024.0
    return "%d B" % num_bytes


def is_path_inside(path, directory):
    """True if `path` is `directory` or lives inside it (after resolving)."""
    try:
        path = os.path.realpath(path)
        directory = os.path.realpath(directory)
        return os.path.commonpath([path, directory]) == directory
    except ValueError:  # different drives on Windows
        return False


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------

def _is_private_ipv4(ip):
    try:
        a, b, _, _ = (int(x) for x in ip.split("."))
    except ValueError:
        return False
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def _is_usable_ipv4(ip):
    if not ip or ":" in ip:
        return False
    return not (ip.startswith("127.") or ip.startswith("0.") or ip.startswith("169.254."))


def get_lan_ips():
    """Return candidate LAN IPv4 addresses of this computer, best first.

    Uses the "UDP connect" trick (no packet is sent) to find the address of
    the interface used for the default route, plus the host name addresses.
    Private addresses (192.168.x.x, 10.x.x.x, 172.16-31.x.x) come first.
    """
    found = []

    def add(ip):
        if _is_usable_ipv4(ip) and ip not in found:
            found.append(ip)

    for probe in ("192.168.1.1", "10.255.255.255", "8.8.8.8"):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.2)
            sock.connect((probe, 80))
            add(sock.getsockname()[0])
        except OSError:
            pass
        finally:
            if sock is not None:
                sock.close()

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass

    found.sort(key=lambda ip: (0 if _is_private_ipv4(ip) else 1))
    return found or ["127.0.0.1"]


def build_url(host, port, token=None):
    url = "http://%s:%d/" % (host, port)
    if token:
        url += "?token=" + token
    return url
