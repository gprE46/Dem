import struct
import unittest
import zlib

import helpers  # noqa: F401
from blender_drop.qr import QrCode


class QrTests(unittest.TestCase):
    def test_version_and_size(self):
        url = "http://192.168.1.35:8765/?token=" + "x" * 22
        qr = QrCode(url)
        self.assertEqual(qr.size, qr.version * 4 + 17)
        self.assertLessEqual(qr.version, 5)
        # finder pattern corners are dark, separator is light
        self.assertTrue(qr.modules[0][0])
        self.assertFalse(qr.modules[7][7])
        self.assertTrue(qr.modules[qr.size - 8][8])  # always-dark module

    def test_png_is_valid(self):
        png = QrCode("hello").to_png(scale=4, border=4)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", png[16:24])
        self.assertEqual(width, height)
        # walk chunks and check CRCs
        pos = 8
        while pos < len(png):
            length = struct.unpack(">I", png[pos:pos + 4])[0]
            tag = png[pos + 4:pos + 8]
            data = png[pos + 8:pos + 8 + length]
            crc = struct.unpack(">I", png[pos + 8 + length:pos + 12 + length])[0]
            self.assertEqual(crc, zlib.crc32(tag + data) & 0xFFFFFFFF)
            pos += 12 + length

    def test_decodes_with_real_reader_if_available(self):
        try:
            import zxingcpp
            from PIL import Image
        except ImportError:
            self.skipTest("zxing-cpp / Pillow not installed")
        import io
        for text in ["http://192.168.1.35:8765/?token=8F3A91abcDEF_-123456", "a" * 300, "ñandú ✓"]:
            img = Image.open(io.BytesIO(QrCode(text).to_png(scale=4)))
            self.assertEqual(zxingcpp.read_barcodes(img)[0].text, text)

    def test_too_long(self):
        with self.assertRaises(ValueError):
            QrCode("x" * 5000)


if __name__ == "__main__":
    unittest.main()
