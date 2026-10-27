import http.client
import json
import os
import queue
import shutil
import socket
import tempfile
import time
import unittest
from urllib.parse import quote

import helpers
from blender_drop import jobs, server

WEB_DIR = os.path.join(helpers.ADDON_DIR, "blender_drop", "web")
TOKEN = "test-token-123"
ALLOWED = {".glb", ".obj", ".png", ".blend", ".zip"}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="blender_drop_test_")
        self.store = jobs.JobStore()
        self.queue = jobs.new_queue()
        config = server.ServerConfig(
            port=0, token=TOKEN, upload_dir=self.tmp, web_dir=WEB_DIR,
            max_bytes=5 * 1024 * 1024, allowed_extensions=ALLOWED,
            host="127.0.0.1", info={"blender": "test"})
        self.srv = server.DropServer(config, self.store, self.queue)
        self.srv.start()
        self.port = self.srv.port

    def tearDown(self):
        self.srv.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, data, res

    def upload_multipart(self, filename, content, token=TOKEN, x_filename=True):
        boundary = b"----BlenderDropTest7MA4YWxk"
        body = helpers.build_multipart(boundary, [("file", filename, content)])
        headers = {"Content-Type": "multipart/form-data; boundary=" + boundary.decode()}
        if x_filename:
            headers["X-Filename"] = quote(filename)
        status, data, _ = self.request("POST", "/upload?token=" + token, body, headers)
        return status, json.loads(data.decode("utf-8"))

    # -- tests -------------------------------------------------------------

    def test_index_and_static_files(self):
        for path, ctype in (("/", "text/html"), ("/app.js", "javascript"), ("/style.css", "text/css")):
            status, data, res = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(ctype, res.getheader("Content-Type"))
            self.assertTrue(data)
        status, _, res = self.request("GET", "/")
        self.assertIn("default-src 'self'", res.getheader("Content-Security-Policy"))

    def test_unknown_paths_and_traversal(self):
        for path in ("/../server.py", "/web/../server.py", "/etc/passwd", "/%2e%2e/server.py"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 404, path)

    def test_token_required(self):
        self.assertEqual(self.request("GET", "/status")[0], 401)
        self.assertEqual(self.request("GET", "/status?token=wrong")[0], 403)
        status, data, _ = self.request("GET", "/api/ping?token=" + TOKEN)
        self.assertEqual(status, 200)
        info = json.loads(data)
        self.assertEqual(info["blender"], "test")
        self.assertIn(".glb", info["extensions"])
        status, body = self.upload_multipart("a.glb", b"glTF", token="bad")
        self.assertEqual(status, 403)
        self.assertTrue(self.queue.empty())
        self.assertEqual(os.listdir(self.tmp), [])

    def test_header_token(self):
        status, _, _ = self.request("GET", "/status", headers={"X-BlenderDrop-Token": TOKEN})
        self.assertEqual(status, 200)

    def test_multipart_upload_unicode_name(self):
        content = os.urandom(300_000)
        name = "Silla de jardín ñ ✓ con espacios.glb"
        status, body = self.upload_multipart(name, content)
        self.assertEqual(status, 202, body)
        job = body["job"]
        self.assertEqual(job["state"], jobs.QUEUED)
        self.assertEqual(job["filename"], name)
        self.assertEqual(self.queue.get_nowait(), job["id"])
        raw = self.store.get_job(job["id"])
        with open(raw.path, "rb") as handle:
            self.assertEqual(handle.read(), content)
        self.assertEqual(os.path.basename(raw.path), name)
        self.assertTrue(raw.path.startswith(self.tmp))
        # status endpoints
        status, data, _ = self.request("GET", "/status?token=%s&job=%s" % (TOKEN, job["id"]))
        self.assertEqual(json.loads(data)["job"]["state"], jobs.QUEUED)
        status, data, _ = self.request("GET", "/status?token=" + TOKEN)
        self.assertEqual(json.loads(data)["jobs"][0]["id"], job["id"])

    def test_multipart_filename_from_part_and_sanitized(self):
        status, body = self.upload_multipart("../../evil name.obj", b"v 0 0 0\n", x_filename=False)
        self.assertEqual(status, 202, body)
        self.assertEqual(body["job"]["filename"], "evil name.obj")
        raw = self.store.get_job(body["job"]["id"])
        self.assertEqual(os.path.dirname(raw.path), os.path.join(self.tmp, raw.id))

    def test_raw_upload(self):
        status, data, _ = self.request("POST", "/upload?token=" + TOKEN, b"PNGDATA",
                                       {"X-Filename": quote("textura ñ.png"),
                                        "Content-Type": "application/octet-stream"})
        self.assertEqual(status, 202)
        job = json.loads(data)["job"]
        with open(self.store.get_job(job["id"]).path, "rb") as handle:
            self.assertEqual(handle.read(), b"PNGDATA")

    def test_unsupported_extension(self):
        status, body = self.upload_multipart("virus.exe", b"MZ")
        self.assertEqual(status, 415)
        self.assertEqual(body["error"], "unsupported_type")
        status, body = self.upload_multipart("virus.exe", b"MZ", x_filename=False)
        self.assertEqual(status, 415)
        self.assertEqual(os.listdir(self.tmp), [])
        self.assertTrue(self.queue.empty())

    def test_too_large(self):
        status, body = self.upload_multipart("big.glb", b"x" * (5 * 1024 * 1024 + 10))
        self.assertEqual(status, 413)
        self.assertEqual(body["error"], "too_large")

    def test_empty_file(self):
        status, body = self.upload_multipart("empty.glb", b"")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "empty_file")

    def test_connection_lost_mid_upload(self):
        sock = socket.create_connection(("127.0.0.1", self.port))
        headers = ("POST /upload?token=%s HTTP/1.1\r\nHost: x\r\nContent-Length: 100000\r\n"
                   "X-Filename: lost.glb\r\nContent-Type: application/octet-stream\r\n\r\n" % TOKEN)
        sock.sendall(headers.encode() + b"x" * 1000)
        time.sleep(0.2)
        sock.close()
        deadline = time.time() + 5
        while time.time() < deadline:
            recent = self.store.recent()
            if recent and recent[0]["state"] == jobs.ERROR:
                break
            time.sleep(0.05)
        self.assertEqual(recent[0]["state"], jobs.ERROR)
        self.assertEqual(recent[0]["error_code"], "connection_lost")
        self.assertEqual(os.listdir(self.tmp), [])   # partial file removed
        self.assertTrue(self.queue.empty())
        # server still works after the failure
        self.assertEqual(self.request("GET", "/api/ping?token=" + TOKEN)[0], 200)

    def test_missing_length_and_garbage(self):
        status, _, _ = self.request("POST", "/upload?token=" + TOKEN, b"abc",
                                    {"Content-Type": "text/plain", "X-Filename": ""})
        self.assertEqual(status, 400)
        status, body = 0, None
        boundary = "zzz"
        status, data, _ = self.request("POST", "/upload?token=" + TOKEN, b"--zzz\r\nbroken",
                                       {"Content-Type": "multipart/form-data; boundary=" + boundary})
        self.assertEqual(status, 400)

    def test_concurrent_uploads(self):
        import threading
        results = []

        def worker(i):
            results.append(self.upload_multipart("model_%d.glb" % i, os.urandom(50_000))[0])

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(results, [202] * 8)
        self.assertEqual(self.queue.qsize(), 8)

    def test_stop_is_quick_and_releases_port(self):
        start = time.time()
        self.srv.stop()
        self.assertLess(time.time() - start, 3)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
        self.srv.start()  # restart works (tearDown stops it again)


if __name__ == "__main__":
    unittest.main()
