"""Streaming multipart/form-data parser (no bpy, no ``cgi`` module).

``cgi`` was removed in Python 3.13 (used by Blender 5.1+), and reading a
whole upload into memory is not acceptable for large files, so this module
implements a small push parser: feed it chunks, it calls back with part
headers and body data as they arrive. Memory use stays bounded.
"""

import re
from urllib.parse import unquote

MAX_HEADER_BYTES = 16 * 1024


class MultipartError(ValueError):
    pass


def get_boundary(content_type):
    """Extract the boundary from a Content-Type header value."""
    if not content_type:
        return None
    main, params = parse_header_value(content_type)
    if main.lower() != "multipart/form-data":
        return None
    boundary = params.get("boundary")
    if not boundary or len(boundary) > 200:
        return None
    return boundary.encode("latin-1", "replace")


_TOKEN_RE = re.compile(r'\s*([^\s=;]+)\s*(?:=\s*("(?:[^"\\]|\\.)*"|[^;]*))?\s*(?:;|$)')


def parse_header_value(value):
    """Parse 'main; key=value; key2="quoted"' into (main, {key: value}).

    Handles quoted strings with backslash escapes and RFC 5987
    ``filename*=UTF-8''...`` parameters (decoded into ``filename``).
    """
    if ";" in value:
        main, rest = value.split(";", 1)
    else:
        main, rest = value, ""
    params = {}
    extended = {}
    pos = 0
    while pos < len(rest):
        match = _TOKEN_RE.match(rest, pos)
        if not match or match.end() == pos:
            break
        pos = match.end()
        key = match.group(1).lower()
        raw = match.group(2)
        if raw is None:
            continue
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            raw = re.sub(r"\\(.)", r"\1", raw[1:-1])
        if key.endswith("*"):
            extended[key[:-1]] = raw
        else:
            params[key] = raw
    for key, raw in extended.items():
        # charset'language'percent-encoded
        parts = raw.split("'", 2)
        if len(parts) == 3:
            charset = parts[0] or "utf-8"
            try:
                params[key] = unquote(parts[2], encoding=charset, errors="replace")
            except LookupError:
                params[key] = unquote(parts[2], errors="replace")
    return main.strip(), params


def _decode_header_bytes(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse_part_headers(block):
    headers = {}
    for line in block.split(b"\r\n"):
        if not line:
            continue
        if b":" not in line:
            raise MultipartError("Malformed part header")
        name, value = line.split(b":", 1)
        headers[name.decode("latin-1").strip().lower()] = _decode_header_bytes(value.strip())
    return headers


def part_disposition(headers):
    """Return (field_name, filename_or_None) from part headers."""
    disposition = headers.get("content-disposition", "")
    _, params = parse_header_value(disposition)
    filename = params.get("filename")
    if filename is not None:
        # Browsers (HTML spec) percent-encode only these three characters.
        filename = (filename.replace("%22", '"').replace("%0D", "\r")
                    .replace("%0A", "\n"))
    return params.get("name", ""), filename


class MultipartParser:
    """Push parser. Callbacks:

    on_part_begin(headers: dict) -> None
    on_part_data(chunk: bytes) -> None
    on_part_end() -> None
    """

    _PREAMBLE, _AFTER_DELIM, _HEADERS, _BODY, _END = range(5)

    def __init__(self, boundary, on_part_begin, on_part_data, on_part_end):
        if isinstance(boundary, str):
            boundary = boundary.encode("latin-1")
        self._dash_boundary = b"--" + boundary
        self._delimiter = b"\r\n--" + boundary
        self._buf = b""
        self._state = self._PREAMBLE
        self._on_begin = on_part_begin
        self._on_data = on_part_data
        self._on_end = on_part_end

    @property
    def finished(self):
        return self._state == self._END

    def feed(self, data):
        if self._state == self._END:
            return
        self._buf += data
        while True:
            state = self._state
            buf = self._buf

            if state == self._PREAMBLE:
                idx = buf.find(self._dash_boundary)
                if idx < 0:
                    keep = len(self._dash_boundary) - 1
                    self._buf = buf[-keep:] if len(buf) > keep else buf
                    return
                self._buf = buf[idx + len(self._dash_boundary):]
                self._state = self._AFTER_DELIM

            elif state == self._AFTER_DELIM:
                if len(buf) < 2:
                    return
                if buf[:2] == b"--":
                    self._state = self._END
                    self._buf = b""
                    return
                idx = buf.find(b"\r\n")
                if idx < 0:
                    if len(buf) > 1024:
                        raise MultipartError("Malformed boundary line")
                    return
                if buf[:idx].strip(b" \t"):
                    raise MultipartError("Malformed boundary line")
                self._buf = buf[idx + 2:]
                self._state = self._HEADERS

            elif state == self._HEADERS:
                if buf.startswith(b"\r\n"):
                    headers, rest = {}, buf[2:]
                else:
                    idx = buf.find(b"\r\n\r\n")
                    if idx < 0:
                        if len(buf) > MAX_HEADER_BYTES:
                            raise MultipartError("Part headers too large")
                        return
                    headers, rest = parse_part_headers(buf[:idx]), buf[idx + 4:]
                self._buf = rest
                self._state = self._BODY
                self._on_begin(headers)

            elif state == self._BODY:
                idx = buf.find(self._delimiter)
                if idx < 0:
                    safe = len(buf) - (len(self._delimiter) - 1)
                    if safe > 0:
                        self._on_data(buf[:safe])
                        self._buf = buf[safe:]
                    return
                if idx:
                    self._on_data(buf[:idx])
                self._buf = buf[idx + len(self._delimiter):]
                self._state = self._AFTER_DELIM
                self._on_end()

            else:
                return

    def close(self):
        if self._state != self._END:
            raise MultipartError("Upload incomplete: the connection ended before the file was fully received")
