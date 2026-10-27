"""Install dist/BlenderDrop.zip into a clean Blender profile and use it.

    python tests/blender_install_test.py dist/BlenderDrop.zip extension
    python tests/blender_install_test.py dist/BlenderDrop.zip legacy
(or:  blender -b --factory-startup --python tests/blender_install_test.py -- <zip> <mode>)
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import time

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
ZIP, MODE = os.path.abspath(args[0]), args[1]
os.environ.setdefault("BLENDER_USER_RESOURCES", tempfile.mkdtemp(prefix="bd_profile_"))

import bpy  # noqa: E402
import addon_utils  # noqa: E402

failures = []


def check(cond, label):
    print(("  PASS " if cond else "  FAIL ") + label)
    if not cond:
        failures.append(label)


bpy.ops.wm.read_homefile(use_factory_startup=True)
if MODE == "extension":
    bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
    module = "bl_ext.user_default.blender_drop"
else:
    bpy.ops.preferences.addon_install(filepath=ZIP)
    addon_utils.enable("blender_drop", default_set=True)
    module = "blender_drop"

check(module in bpy.context.preferences.addons, "installed and enabled as %s" % module)
runtime = sys.modules[module + ".runtime"]
check(runtime.prefs() is not None, "preferences found")
print("  data dir:", runtime.data_dir())
check(not runtime.data_dir().startswith(os.path.dirname(sys.modules[module].__file__)),
      "data folder is outside the add-on folder")
runtime.prefs().port = 18765
check(bpy.ops.blender_drop.start_server() == {"FINISHED"}, "server starts")

# export a cube to glb and upload it like the phone does
glb = os.path.join(tempfile.mkdtemp(), "Cubo Prueba.glb")
bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
bpy.context.active_object.name = "SentCube"
bpy.ops.export_scene.gltf(filepath=glb, use_selection=True)
bpy.data.objects.remove(bpy.data.objects["SentCube"])

result = {}


def send():
    with open(glb, "rb") as fh:
        data = fh.read()
    b = "----bd"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"Cubo Prueba.glb\"\r\n"
             "Content-Type: model/gltf-binary\r\n\r\n" % b).encode() + data + ("\r\n--%s--\r\n" % b).encode())
    c = http.client.HTTPConnection("127.0.0.1", 18765, timeout=20)
    c.request("POST", "/upload?token=" + runtime.state.token, body,
              {"Content-Type": "multipart/form-data; boundary=" + b})
    r = c.getresponse()
    result["status"] = r.status
    result["job"] = json.loads(r.read())["job"]


th = threading.Thread(target=send)
th.start()
deadline = time.time() + 30
while time.time() < deadline:
    runtime.process_pending()
    if not th.is_alive():
        job = runtime.state.store.get(result.get("job", {}).get("id", ""))
        if job and job["state"] in ("IMPORTED", "ERROR"):
            break
    time.sleep(0.01)
check(result.get("status") == 202, "upload accepted (202)")
job = runtime.state.store.get(result["job"]["id"])
check(job["state"] == "IMPORTED", "imported: %s" % job["message"])
coll = bpy.data.collections.get("Cubo Prueba")
check(coll is not None and coll.objects and coll.objects[0].name.startswith("SentCube"),
      "object is in Blender Drop / Cubo Prueba")
check(bpy.data.collections["Blender Drop"].children.get("Cubo Prueba") is not None, "collection nested under 'Blender Drop'")

bpy.ops.blender_drop.stop_server()
check(not runtime.is_running(), "server stops")
if MODE == "extension":
    bpy.ops.extensions.package_uninstall(repo_index=[r.module for r in bpy.context.preferences.extensions.repos].index("user_default"), pkg_id="blender_drop")
else:
    addon_utils.disable("blender_drop", default_set=True)
check(module not in bpy.context.preferences.addons, "disabled/uninstalled cleanly")
print("RESULT", MODE, "FAILED" if failures else "OK")
sys.exit(1 if failures else 0)
