"""Local HTTP server (runs in background threads, NEVER touches bpy).

Responsibilities:
  * serve the mobile web page (web/index.html, style.css, app.js)
  * receive uploads (POST /upload, multipart/form-data or raw body)
  * stream them to disk, then put the job id on a thread-safe queue
  * report job states (GET /status) and server info (GET /api/ping)

The Blender side (runtime.py) consumes the queue from the main thread.
"""

import hmac
import json
import os
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from . import jobs
from .multipart import MultipartError, MultipartParser, get_boundary, part_disposition
from .utils import get_extension, sanitize_filename

APP_VERSION = "1.0.0"
CHUNK_SIZE = 256 * 1024
MULTIPART_OVERHEAD = 1024 * 1024        # allowed extra bytes for multipart framing
MIN_FREE_DISK = 200 * 1024 * 1024        # keep at least 200 MB free
SOCKET_TIMEOUT = 60                      # seconds without data -> connection lost

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}

SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cache-Control", "no-store"),
)
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class UploadError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class ClientGone(Exception):
    """The phone stopped sending data (Wi-Fi lost, page closed, timeout)."""


class ServerConfig:
    def __init__(self, port, token, upload_dir, web_dir, max_bytes,
                 allowed_extensions, host="0.0.0.0", info=None, verbose=False):
        self.host = host
        self.port = int(port)
        self.token = token
        self.upload_dir = upload_dir
        self.web_dir = web_dir
        self.max_bytes = int(max_bytes)
        self.allowed_extensions = frozenset(allowed_extensions)
        self.info = dict(info or {})
        self.verbose = verbose


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets two programs bind the same port: avoid it.
    allow_reuse_address = os.name != "nt"
    request_queue_size = 32

    def handle_error(self, request, client_address):
        # Never let a broken request kill the server; keep the log short.
        import traceback
        print("[Blender Drop] request error from %s:" % (client_address[0],))
        traceback.print_exc()


class DropServer:
    def __init__(self, config, store, job_queue):
        self.config = config
        self.store = store
        self.queue = job_queue
        self._httpd = None
        self._thread = None
        self._stopping = False
        self._connections = set()
        self._conn_lock = threading.Lock()
        self.started_at = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self):
        return self._httpd is not None and not self._stopping

    @property
    def stopping(self):
        return self._stopping

    @property
    def port(self):
        return self._httpd.server_address[1] if self._httpd else self.config.port

    def start(self):
        if self._httpd is not None:
            return
        os.makedirs(self.config.upload_dir, exist_ok=True)
        httpd = _HTTPServer((self.config.host, self.config.port), DropRequestHandler)
        httpd.drop = self
        self._httpd = httpd
        self._stopping = False
        self.started_at = time.time()
        self._thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.25},
            name="BlenderDropHTTP", daemon=True)
        self._thread.start()

    def stop(self):
        httpd = self._httpd
        if httpd is None:
            return
        self._stopping = True
        try:
            httpd.shutdown()        # waits for serve_forever to exit (<= poll_interval)
        finally:
            httpd.server_close()
            with self._conn_lock:
                conns = list(self._connections)
            for conn in conns:     # unblock threads waiting on slow uploads
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            if self._thread is not None:
                self._thread.join(timeout=2.0)
            self._httpd = None
            self._thread = None

    def _track(self, conn, add):
        with self._conn_lock:
            if add:
                self._connections.add(conn)
            else:
                self._connections.discard(conn)

    def info(self):
        cfg = self.config
        data = {
            "ok": True,
            "app": "Blender Drop",
            "version": APP_VERSION,
            "max_upload_bytes": cfg.max_bytes,
            "extensions": sorted(cfg.allowed_extensions),
            "queue": self.queue.qsize(),
        }
        data.update(cfg.info)
        return data


class DropRequestHandler(BaseHTTPRequestHandler):
    server_version = "BlenderDrop/" + APP_VERSION
    protocol_version = "HTTP/1.1"
    timeout = SOCKET_TIMEOUT

    # -- plumbing ----------------------------------------------------------

    @property
    def drop(self):
        return self.server.drop

    def setup(self):
        super().setup()
        self.drop._track(self.connection, True)

    def finish(self):
        try:
            super().finish()
        finally:
            self.drop._track(self.connection, False)

    def log_message(self, fmt, *args):
        if self.drop.config.verbose:
            print("[Blender Drop] %s - %s" % (self.address_string(), fmt % args))

    def log_error(self, fmt, *args):
        print("[Blender Drop] %s - %s" % (self.address_string(), fmt % args))

    def _send(self, status, body, content_type, close=False, extra_headers=()):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in SECURITY_HEADERS:
                self.send_header(key, value)
            for key, value in extra_headers:
                self.send_header(key, value)
            if close:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (ConnectionError, TimeoutError, OSError):
            self.close_connection = True

    def _json(self, status, payload, close=False):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", close=close)

    def _error(self, status, code, message, close=False):
        self._json(status, {"ok": False, "error": code, "message": message}, close=close)

    def _query(self):
        parts = urlsplit(self.path)
        return parts.path, parse_qs(parts.query)

    def _check_token(self, query):
        supplied = self.headers.get("X-BlenderDrop-Token") or (query.get("token") or [""])[0]
        if not supplied:
            self._error(401, "missing_token",
                        "Missing token. Open the link shown in Blender (Copy URL or QR).", close=True)
            return False
        if not hmac.compare_digest(supplied.encode("utf-8"), self.drop.config.token.encode("utf-8")):
            self._error(403, "invalid_token",
                        "Invalid token. Blender was restarted or the link is old: scan the QR again.",
                        close=True)
            return False
        return True

    # -- GET ---------------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path, query = self._query()
        if self.drop.stopping:
            return self._error(503, "server_stopped", "Blender Drop server is stopping.", close=True)

        if path in STATIC_FILES:
            return self._serve_static(*STATIC_FILES[path])
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if path in ("/api/ping", "/ping"):
            if not self._check_token(query):
                return
            return self._json(200, self.drop.info())
        if path in ("/status", "/api/status"):
            if not self._check_token(query):
                return
            job_id = (query.get("job") or [""])[0]
            if job_id:
                job = self.drop.store.get(job_id)
                if job is None:
                    return self._error(404, "unknown_job", "This upload is no longer known by Blender.")
                return self._json(200, {"ok": True, "job": job})
            payload = self.drop.info()
            payload["jobs"] = self.drop.store.recent(20)
            return self._json(200, payload)
        return self._error(404, "not_found", "Not found")

    def _serve_static(self, filename, content_type):
        full = os.path.join(self.drop.config.web_dir, filename)
        try:
            with open(full, "rb") as handle:
                body = handle.read()
        except OSError:
            return self._error(500, "missing_web_file", "Web interface file missing: " + filename)
        extra = (("Content-Security-Policy", CSP),) if filename.endswith(".html") else ()
        self._send(200, body, content_type, extra_headers=extra)

    # -- POST --------------------------------------------------------------

    def do_POST(self):
        path, query = self._query()
        if path not in ("/upload", "/api/upload"):
            return self._error(404, "not_found", "Not found", close=True)
        if self.drop.stopping:
            return self._error(503, "server_stopped", "Blender Drop server is stopping.", close=True)
        if not self._check_token(query):
            return
        self._handle_upload()

    def _handle_upload(self):
        drop = self.drop
        cfg = drop.config

        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            return self._error(411, "length_required", "Chunked uploads are not supported.", close=True)
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._error(411, "length_required", "Missing Content-Length header.", close=True)
        if length <= 0:
            return self._error(400, "empty_file", "The file is empty.", close=True)

        boundary = get_boundary(self.headers.get("Content-Type", ""))
        header_name = unquote(self.headers.get("X-Filename", "") or "", errors="replace")
        limit = cfg.max_bytes + (MULTIPART_OVERHEAD if boundary else 0)
        if length > limit:
            return self._error(413, "too_large", "File too large. Maximum size is %d MB."
                               % (cfg.max_bytes // (1024 * 1024)), close=True)
        if not boundary and not header_name:
            return self._error(400, "bad_request",
                               "Expected multipart/form-data or an X-Filename header.", close=True)
        if header_name:
            err = self._validate_name(sanitize_filename(header_name))
            if err:
                return self._error(err.status, err.code, err.message, close=True)
        try:
            free = shutil.disk_usage(cfg.upload_dir).free
        except OSError:
            free = None
        if free is not None and free - length < MIN_FREE_DISK:
            return self._error(507, "disk_full", "Not enough free disk space on the computer.", close=True)

        job = drop.store.create(sanitize_filename(header_name) if header_name else "upload",
                                client=self.client_address[0])
        job_dir = os.path.join(cfg.upload_dir, job.id)
        receiver = _FileReceiver(self, job, job_dir, header_name, cfg.max_bytes)
        try:
            os.makedirs(job_dir, exist_ok=False)
            if boundary:
                receiver.receive_multipart(length, boundary)
            else:
                receiver.receive_raw(length)
            final_path = receiver.finalize()
        except ClientGone:
            receiver.abort()
            drop.store.update(job.id, state=jobs.ERROR, error_code="connection_lost",
                              message="Connection lost during upload. Check the Wi-Fi and try again.")
            self.close_connection = True
            return
        except UploadError as exc:
            receiver.abort()
            drop.store.update(job.id, state=jobs.ERROR, error_code=exc.code, message=exc.message)
            return self._error(exc.status, exc.code, exc.message, close=True)
        except MultipartError as exc:
            receiver.abort()
            drop.store.update(job.id, state=jobs.ERROR, error_code="bad_request", message=str(exc))
            return self._error(400, "bad_request", str(exc), close=True)
        except OSError as exc:
            receiver.abort()
            msg = "Could not save the file on the computer: %s" % (exc.strerror or exc)
            drop.store.update(job.id, state=jobs.ERROR, error_code="save_failed", message=msg)
            return self._error(500, "save_failed", msg, close=True)

        drop.store.update(job.id, state=jobs.UPLOADED, message="Uploaded",
                          path=final_path, directory=job_dir, size=receiver.size)
        if drop.stopping:
            receiver.abort()
            drop.store.update(job.id, state=jobs.ERROR, error_code="server_stopped",
                              message="Blender Drop was stopped before importing.")
            return self._error(503, "server_stopped", "Blender Drop server is stopping.", close=True)
        snapshot = drop.store.update(job.id, state=jobs.QUEUED, message="Waiting for Blender")
        drop.queue.put(job.id)
        print("[Blender Drop] received %s (%d bytes) from %s"
              % (receiver.filename, receiver.size, self.client_address[0]))
        self._json(202, {"ok": True, "job": snapshot})

    def _validate_name(self, name):
        ext = get_extension(name)
        if not ext:
            return UploadError(415, "unsupported_type", "The file has no extension, so Blender "
                               "does not know how to import it.")
        if ext not in self.drop.config.allowed_extensions:
            return UploadError(415, "unsupported_type",
                               "'%s' files are not supported by this Blender installation." % ext)
        return None


class _FileReceiver:
    """Streams one uploaded file to <job_dir>/<name>.part."""

    def __init__(self, handler, job, job_dir, header_name, max_bytes):
        self.handler = handler
        self.job = job
        self.job_dir = job_dir
        self.header_name = header_name
        self.max_bytes = max_bytes
        self.filename = None
        self.part_path = None
        self.size = 0
        self._file = None
        self._file_parts = 0
        self._in_file = False
        self._last_progress = 0.0
        self._total = 0

    # -- streaming ---------------------------------------------------------

    def _read_body(self, length, sink):
        rfile = self.handler.rfile
        remaining = length
        self._total = length
        drop = self.handler.drop
        while remaining > 0:
            try:
                chunk = rfile.read(min(CHUNK_SIZE, remaining))
            except (TimeoutError, ConnectionError, OSError):
                raise ClientGone()
            if not chunk:
                raise ClientGone()
            remaining -= len(chunk)
            sink(chunk)
            if drop.stopping:
                raise UploadError(503, "server_stopped", "Blender Drop was stopped during the upload.")
            self._report_progress(length - remaining)

    def _report_progress(self, received):
        now = time.monotonic()
        if now - self._last_progress < 0.5:
            return
        self._last_progress = now
        pct = int(received * 100 / self._total) if self._total else 0
        self.handler.drop.store.update(self.job.id, size=self.size, message="Receiving %d%%" % pct)

    def receive_raw(self, length):
        self._open(self.header_name)
        self._read_body(length, self._write)
        self._close_file()

    def receive_multipart(self, length, boundary):
        parser = MultipartParser(boundary, self._on_begin, self._on_data, self._on_end)
        self._read_body(length, parser.feed)
        parser.close()

    def _on_begin(self, headers):
        _, filename = part_disposition(headers)
        if filename is None:
            self._in_file = False      # ordinary form field: ignored
            return
        self._file_parts += 1
        if self._file_parts > 1:
            raise UploadError(400, "bad_request", "Send one file per request.")
        self._open(self.header_name or filename)
        self._in_file = True

    def _on_data(self, chunk):
        if self._in_file:
            self._write(chunk)

    def _on_end(self):
        if self._in_file:
            self._close_file()
            self._in_file = False

    # -- file handling -----------------------------------------------------

    def _open(self, raw_name):
        name = sanitize_filename(raw_name or "")
        err = self.handler._validate_name(name)
        if err:
            raise err
        self.filename = name
        self.part_path = os.path.join(self.job_dir, name + ".part")
        self._file = open(self.part_path, "wb")
        self.handler.drop.store.update(self.job.id, filename=name)

    def _write(self, chunk):
        self.size += len(chunk)
        if self.size > self.max_bytes:
            raise UploadError(413, "too_large", "File too large. Maximum size is %d MB."
                              % (self.max_bytes // (1024 * 1024)))
        self._file.write(chunk)

    def _close_file(self):
        if self._file is not None:
            self._file.close()
            self._file = None

    def finalize(self):
        self._close_file()
        if not self.part_path:
            raise UploadError(400, "bad_request", "No file was found in the upload.")
        if self.size == 0:
            raise UploadError(400, "empty_file", "The file is empty.")
        final_path = self.part_path[:-len(".part")]
        os.replace(self.part_path, final_path)
        return final_path

    def abort(self):
        try:
            self._close_file()
        except OSError:
            pass
        shutil.rmtree(self.job_dir, ignore_errors=True)
