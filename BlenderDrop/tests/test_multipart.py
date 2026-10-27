import os
import random
import unittest

import helpers
from blender_drop.multipart import (MultipartError, MultipartParser, get_boundary,
                                    parse_header_value, part_disposition)


def parse(body, boundary, chunk_sizes=None):
    parts = []

    def begin(headers):
        parts.append({"headers": headers, "data": b"", "ended": False})

    def data(chunk):
        parts[-1]["data"] += chunk

    def end():
        parts[-1]["ended"] = True

    parser = MultipartParser(boundary, begin, data, end)
    pos = 0
    rng = random.Random(42)
    while pos < len(body):
        size = rng.choice(chunk_sizes) if chunk_sizes else len(body)
        parser.feed(body[pos:pos + size])
        pos += size
    parser.close()
    return parts


class MultipartTests(unittest.TestCase):
    boundary = b"----WebKitFormBoundaryAbC123"

    def test_single_file_any_chunking(self):
        payload = os.urandom(200_000) + b"\r\n--" + self.boundary[:-1] + b"\r\n--"  # near-miss boundary
        body = helpers.build_multipart(self.boundary, [("file", "chair.glb", payload)])
        for sizes in ([1, 2, 3], [7, 64, 1000], [65536], [len(body)]):
            parts = parse(body, self.boundary, sizes if len(sizes) > 1 or sizes[0] < len(body) else None)
            self.assertEqual(len(parts), 1)
            self.assertEqual(parts[0]["data"], payload)
            self.assertTrue(parts[0]["ended"])
            self.assertEqual(part_disposition(parts[0]["headers"]), ("file", "chair.glb"))

    def test_fields_and_unicode_filename(self):
        body = helpers.build_multipart(self.boundary, [
            ("token", None, b"abc"),
            ("file", "Silla jardín ✓.obj", b"v 0 0 0\n"),
        ])
        parts = parse(body, self.boundary, [5, 11])
        self.assertEqual(parts[0]["data"], b"abc")
        self.assertEqual(part_disposition(parts[1]["headers"]), ("file", "Silla jardín ✓.obj"))

    def test_empty_file_and_crlf_content(self):
        body = helpers.build_multipart(self.boundary, [("a", "x.txt", b""), ("b", "y.txt", b"\r\n\r\n")])
        parts = parse(body, self.boundary, [3])
        self.assertEqual(parts[0]["data"], b"")
        self.assertEqual(parts[1]["data"], b"\r\n\r\n")

    def test_incomplete_body_raises(self):
        body = helpers.build_multipart(self.boundary, [("file", "a.glb", b"12345")])
        with self.assertRaises(MultipartError):
            parse(body[:-30], self.boundary)

    def test_preamble_is_ignored(self):
        body = b"garbage preamble\r\n" + helpers.build_multipart(self.boundary, [("f", "a.png", b"PNG")])
        self.assertEqual(parse(body, self.boundary)[0]["data"], b"PNG")

    def test_get_boundary(self):
        self.assertEqual(get_boundary('multipart/form-data; boundary="abc def"'), b"abc def")
        self.assertEqual(get_boundary("multipart/form-data; boundary=xyz"), b"xyz")
        self.assertIsNone(get_boundary("application/octet-stream"))
        self.assertIsNone(get_boundary(""))

    def test_rfc5987_and_escaped_quotes(self):
        _, params = parse_header_value("form-data; name=\"f\"; filename*=UTF-8''ni%C3%B1o%20%E2%9C%93.glb")
        self.assertEqual(params["filename"], "niño ✓.glb")
        _, filename = part_disposition({"content-disposition": 'form-data; name="f"; filename="a%22b.glb"'})
        self.assertEqual(filename, 'a"b.glb')


if __name__ == "__main__":
    unittest.main()
