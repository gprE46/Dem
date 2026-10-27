"""Real browser test: Blender (bpy) runs the add-on server, Chromium with
iPhone emulation opens the page, uploads a .glb and waits for the result.

    python tests/web_e2e.py      (needs `bpy` + Node Playwright with Chromium)
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "addon"))
import bpy  # noqa: E402
import addon_utils  # noqa: E402

out_dir = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
bpy.ops.wm.read_homefile(use_factory_startup=True)
glb = os.path.join(tempfile.mkdtemp(), "Silla Jardín.glb")
bpy.ops.mesh.primitive_cylinder_add(location=(0, 4, 0))
bpy.context.active_object.name = "Silla"
bpy.ops.export_scene.gltf(filepath=glb, use_selection=True)
bpy.data.objects.remove(bpy.data.objects["Silla"])

addon_utils.enable("blender_drop", default_set=True)
from blender_drop import runtime  # noqa: E402
runtime.prefs().port = 18766
runtime.start_server()
url = runtime.server_url().replace(runtime.state.host, "127.0.0.1")

ok = True
for locale in ("es-ES", "en-US"):
    shot = os.path.join(out_dir, "mobile_%s.png" % locale)
    proc = subprocess.Popen(["node", os.path.join(HERE, "web_e2e.js"), url, glb, shot, locale],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while proc.poll() is None:
        runtime.process_pending()     # what bpy.app.timers does inside Blender
        time.sleep(0.01)
    out, err = proc.communicate()
    if proc.returncode != 0:
        print(err)
        ok = False
        continue
    res = json.loads(out.strip().splitlines()[-1])
    print(locale, json.dumps(res, ensure_ascii=False, indent=1))
    ok &= res["final"] in ("Importado correctamente ✓", "Imported successfully ✓")
    ok &= not res["errors"]
    print("screenshot:", shot)

objs = [o.name for o in bpy.data.collections.get("Blender Drop").all_objects]
print("objects in Blender Drop:", objs)
ok &= len(objs) == 2
runtime.stop_server()
print("RESULT", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
