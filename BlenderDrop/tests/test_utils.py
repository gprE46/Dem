import unittest

import helpers  # noqa: F401  (adds addon/ to sys.path)
from blender_drop import utils


class SanitizeTests(unittest.TestCase):
    def test_keeps_spaces_unicode_and_accents(self):
        self.assertEqual(utils.sanitize_filename("Silla de jardín ñandú.glb"), "Silla de jardín ñandú.glb")
        self.assertEqual(utils.sanitize_filename("椅子 モデル.fbx"), "椅子 モデル.fbx")

    def test_strips_directories_and_traversal(self):
        self.assertEqual(utils.sanitize_filename("../../etc/passwd.obj"), "passwd.obj")
        self.assertEqual(utils.sanitize_filename("C:\\Users\\me\\robot.glb"), "robot.glb")
        self.assertEqual(utils.sanitize_filename(".."), "file")
        self.assertEqual(utils.sanitize_filename(""), "file")
        self.assertEqual(utils.sanitize_filename(None), "file")

    def test_forbidden_and_control_characters(self):
        self.assertEqual(utils.sanitize_filename('a<b>c:d"e|f?g*h.stl'), "a_b_c_d_e_f_g_h.stl")
        self.assertEqual(utils.sanitize_filename("bad\x00name\r\n.obj"), "badname.obj")
        self.assertEqual(utils.sanitize_filename("evil\u202egpj.glb"), "evilgpj.glb")

    def test_windows_reserved_names(self):
        self.assertEqual(utils.sanitize_filename("CON.glb"), "_CON.glb")
        self.assertEqual(utils.sanitize_filename("lpt1.obj"), "_lpt1.obj")

    def test_long_names_are_truncated_keeping_extension(self):
        name = utils.sanitize_filename("á" * 400 + ".gltf")
        self.assertTrue(name.endswith(".gltf"))
        self.assertLessEqual(len(name.encode("utf-8")), utils.MAX_NAME_BYTES)
        name.encode("utf-8").decode("utf-8")  # still valid UTF-8

    def test_trailing_dots_and_spaces(self):
        self.assertEqual(utils.sanitize_filename("  model .glb.  "), "model.glb")


class ExtensionTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(utils.file_kind("a.GLB"), utils.KIND_MODEL)
        self.assertEqual(utils.file_kind("a.svg"), utils.KIND_VECTOR)
        self.assertEqual(utils.file_kind("a.Exr"), utils.KIND_IMAGE)
        self.assertEqual(utils.file_kind("a.zip"), utils.KIND_ARCHIVE)
        self.assertIsNone(utils.file_kind("a.exe"))
        self.assertIsNone(utils.file_kind("noext"))

    def test_requested_formats_are_known(self):
        for ext in (".blend .fbx .obj .glb .gltf .abc .usd .usda .usdc .stl .ply .dae .x3d .svg "
                    ".png .jpg .jpeg .tif .tiff .exr .hdr .bmp .tga").split():
            self.assertIn(ext, utils.ALL_EXTENSIONS)


class NetworkTests(unittest.TestCase):
    def test_lan_ips(self):
        ips = utils.get_lan_ips()
        self.assertTrue(ips)
        for ip in ips:
            self.assertEqual(len(ip.split(".")), 4)

    def test_build_url(self):
        self.assertEqual(utils.build_url("192.168.1.35", 8765, "abc"), "http://192.168.1.35:8765/?token=abc")

    def test_is_path_inside(self):
        self.assertTrue(utils.is_path_inside("/tmp/a/b.png", "/tmp/a"))
        self.assertFalse(utils.is_path_inside("/tmp/ab/b.png", "/tmp/a"))


if __name__ == "__main__":
    unittest.main()
