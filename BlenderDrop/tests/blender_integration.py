"""End-to-end test inside real Blender (no mocks).

Run with Blender:
    blender -b --factory-startup --python tests/blender_integration.py
or with the `bpy` module from PyPI (same Python version as Blender):
    python tests/blender_integration.py

What it does:
  1. exports real test assets with Blender itself (glb, gltf+zip, fbx, obj+zip,
     stl, ply, usd*, abc, svg, png, exr, .blend ...)
  2. loads the default scene (Cube, Camera, Light) = "user's existing scene"
  3. enables the add-on, starts the real HTTP server
  4. uploads every file over HTTP (multipart, like the phone) from a thread
  5. pumps the add-on's main-thread queue step (what bpy.app.timers calls)
  6. checks objects, collections, packed images, cleanup, error handling and
     that the existing scene was not modified.
"""

import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import zipfile
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.join(os.path.dirname(HERE), "addon")
sys.path.insert(0, ADDON_DIR)
sys.path.insert(0, HERE)

import bpy  # noqa: E402
import addon_utils  # noqa: E402

import helpers  # noqa: E402

FAILURES = []
PASSES = []


def check(cond, label):
    (PASSES if cond else FAILURES).append(label)
    print(("  PASS " if cond else "  FAIL ") + label)


# ---------------------------------------------------------------------------
# 1. Test assets
# ---------------------------------------------------------------------------

def make_assets(folder):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_monkey_add(location=(5, 0, 0))
    monkey = bpy.context.active_object
    monkey.name = "Robot"
    mat = bpy.data.materials.new("RobotMat")
    monkey.data.materials.append(mat)
    tex = bpy.data.images.new("tex", 8, 8)
    tex.filepath_raw = os.path.join(folder, "tex.png")
    tex.file_format = "PNG"
    tex.save()
    mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = bpy.data.images.load(os.path.join(folder, "tex.png"))
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
    bpy.ops.object.select_all(action="SELECT")

    assets = {}

    def out(name):
        path = os.path.join(folder, name)
        assets[name] = path
        return path

    bpy.ops.export_scene.gltf(filepath=out("Robot Chair ñ.glb"), export_format="GLB")
    gltf_dir = os.path.join(folder, "gltf_sep")
    os.makedirs(gltf_dir)
    bpy.ops.export_scene.gltf(filepath=os.path.join(gltf_dir, "scene.gltf"), export_format="GLTF_SEPARATE")
    with zipfile.ZipFile(out("gltf_package.zip"), "w") as zf:
        for name in os.listdir(gltf_dir):
            zf.write(os.path.join(gltf_dir, name), "pack/" + name)
        zf.writestr("__MACOSX/._scene.gltf", b"junk")
        zf.writestr("../evil.glb", b"zip slip")
    shutil.copy(os.path.join(gltf_dir, "scene.gltf"), out("lonely.gltf"))  # .bin missing

    bpy.ops.export_scene.fbx(filepath=out("model.fbx"))
    bpy.ops.wm.obj_export(filepath=out("model.obj"))
    with zipfile.ZipFile(out("obj_with_mtl.zip"), "w") as zf:
        zf.write(assets["model.obj"], "model.obj")
        zf.write(os.path.join(folder, "model.mtl"), "model.mtl")
        zf.write(os.path.join(folder, "tex.png"), "tex.png")
    bpy.ops.wm.stl_export(filepath=out("part.stl"))
    bpy.ops.wm.ply_export(filepath=out("scan.ply"))
    bpy.ops.wm.usd_export(filepath=out("asset.usdc"))
    bpy.ops.wm.usd_export(filepath=out("asset.usda"))
    bpy.ops.wm.usd_export(filepath=out("asset.usd"))
    bpy.ops.wm.usd_export(filepath=out("asset.usdz"))
    bpy.ops.wm.alembic_export(filepath=out("anim.abc"), as_background_job=False)

    with open(out("logo.svg"), "w", encoding="utf-8") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
                 '<rect x="10" y="10" width="80" height="80" fill="#f5792a"/>'
                 '<circle cx="50" cy="50" r="20" fill="#000"/></svg>')

    img = bpy.data.images.new("photo", 16, 16)
    img.filepath_raw = out("foto jardín.png")
    img.file_format = "PNG"
    img.save()
    exr = bpy.data.images.new("hdri", 8, 8, float_buffer=True)
    exr.filepath_raw = out("sky.exr")
    exr.file_format = "OPEN_EXR"
    exr.save()
    jpg = bpy.data.images.new("jpg", 8, 8)
    jpg.filepath_raw = out("photo.jpg")
    jpg.file_format = "JPEG"
    jpg.save()

    # .blend with a collection hierarchy + loose object
    bpy.ops.wm.read_factory_settings(use_empty=True)
    props = bpy.data.collections.new("Props")
    bpy.context.scene.collection.children.link(props)
    sub = bpy.data.collections.new("Small Props")
    props.children.link(sub)
    mesh = bpy.data.meshes.new("SphereMesh")
    sphere = bpy.data.objects.new("BlendSphere", mesh)
    props.objects.link(sphere)
    sub.objects.link(bpy.data.objects.new("BlendEmpty", None))
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("LooseEmpty", None))
    bpy.ops.wm.save_as_mainfile(filepath=out("scene.blend"), copy=True)

    with open(out("broken.glb"), "wb") as fh:
        fh.write(b"glTF" + os.urandom(500))
    with open(out("broken.png"), "wb") as fh:
        fh.write(b"not really a png")
    with open(out("old.dae"), "w") as fh:
        fh.write("<COLLADA/>")
    return assets


# ---------------------------------------------------------------------------
# HTTP helpers (run in a worker thread, like the phone)
# ---------------------------------------------------------------------------

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http_upload(port, token, path, result):
    boundary = b"----BlenderDropIntegration"
    with open(path, "rb") as fh:
        content = fh.read()
    name = os.path.basename(path)
    body = helpers.build_multipart(boundary, [("file", name, content)])
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request("POST", "/upload?token=" + token, body=body, headers={
        "Content-Type": "multipart/form-data; boundary=" + boundary.decode(),
        "X-Filename": quote(name)})
    res = conn.getresponse()
    result["status"] = res.status
    result["body"] = json.loads(res.read().decode("utf-8"))
    conn.close()


def http_status(port, token, job_id):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/status?token=%s&job=%s" % (token, job_id))
    data = json.loads(conn.getresponse().read().decode("utf-8"))
    conn.close()
    return data["job"]


def send_and_import(runtime, port, path, timeout=60):
    """Upload from a thread while the main thread runs the timer step."""
    result = {}
    th = threading.Thread(target=http_upload, args=(port, runtime.state.token, path, result))
    th.start()
    deadline = time.time() + timeout
    while th.is_alive() and time.time() < deadline:
        runtime.process_pending()
        time.sleep(0.01)
    th.join()
    if result.get("status") != 202:
        return result.get("status"), result.get("body")
    job_id = result["body"]["job"]["id"]
    seen = set()
    while time.time() < deadline:
        runtime.process_pending()
        job = http_status(port, runtime.state.token, job_id)
        seen.add(job["state"])
        if job["state"] in ("IMPORTED", "ERROR"):
            job["seen"] = seen
            return 202, job
        time.sleep(0.01)
    return 202, {"state": "TIMEOUT"}


def drop_collection(name):
    root = bpy.data.collections.get("Blender Drop")
    for child in root.children:
        if child.name == name:
            return child
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Blender", bpy.app.version_string)
    tmp = tempfile.mkdtemp(prefix="bd_integration_")
    os.makedirs(os.path.join(tmp, "assets"))
    assets = make_assets(os.path.join(tmp, "assets"))

    # 2. the user's scene
    bpy.ops.wm.read_homefile(use_factory_startup=True)
    existing = {o.name: (tuple(o.location), tuple(c.name for c in o.users_collection))
                for o in bpy.data.objects}
    print("Existing scene objects:", sorted(existing))

    # 3. enable the add-on
    mod = addon_utils.enable("blender_drop", default_set=True, handle_error=None)
    check(mod is not None, "add-on enables")
    from blender_drop import runtime, importer
    prefs = runtime.prefs()
    check(prefs is not None, "add-on preferences available")
    prefs.port = free_port()
    prefs.upload_dir = os.path.join(tmp, "uploads")
    check(hasattr(bpy.types, "VIEW3D_PT_blender_drop"), "panel registered")
    check(hasattr(bpy.ops.blender_drop, "start_server"), "operators registered")

    unavailable = importer.unavailable_extensions()
    print("Unavailable in this Blender:", unavailable)
    check(".dae" in unavailable if bpy.app.version >= (5, 0, 0) else True, "collada reported unavailable on 5.x")

    # 4. start
    res = bpy.ops.blender_drop.start_server()
    check(res == {"FINISHED"}, "Start Server operator")
    check(runtime.is_running(), "server running")
    check(bpy.app.timers.is_registered(runtime._timer), "bpy.app.timers queue processor registered")
    port = runtime.state.server.port
    url = runtime.server_url()
    check(url.startswith("http://") and "token=" in url, "URL with token: " + url)
    check(os.path.isfile(runtime.state.qr_path), "QR PNG generated")
    bpy.ops.blender_drop.copy_url()
    check(bpy.context.window_manager.clipboard in (url, ""), "Copy URL operator runs")

    expected = {
        "Robot Chair ñ.glb": 1, "model.fbx": 1, "model.obj": 1, "part.stl": 1, "scan.ply": 1,
        "asset.usdc": 1, "asset.usda": 1, "asset.usd": 1, "asset.usdz": 1, "anim.abc": 1,
        "logo.svg": 1, "scene.blend": 3, "gltf_package.zip": 1, "obj_with_mtl.zip": 1,
    }
    for name, min_objects in expected.items():
        status, job = send_and_import(runtime, port, assets[name])
        ok = status == 202 and job.get("state") == "IMPORTED"
        check(ok, "import %-22s -> %s %s" % (name, job.get("state"), job.get("details") or job.get("message")))
        if ok:
            stem = os.path.splitext(name)[0]
            coll = drop_collection(stem)
            count = len(coll.all_objects) if coll else 0
            check(coll is not None and count >= min_objects,
                  "  collection 'Blender Drop/%s' has %d object(s)" % (stem, count))
            check("IMPORTING" in job["seen"] or "IMPORTED" in job["seen"], "  states observed: %s" % sorted(job["seen"]))

    # .blend hierarchy preserved, no scene replaced
    blend_coll = drop_collection("scene")
    check(blend_coll is not None and "Props" in [c.name for c in blend_coll.children],
          ".blend appended with its collection hierarchy")
    check(len(bpy.data.scenes) == 1, ".blend did not add/replace scenes")
    check(bpy.data.objects.get("LooseEmpty") is not None, ".blend loose objects appended")

    # images
    for name in ("foto jardín.png", "sky.exr", "photo.jpg"):
        status, job = send_and_import(runtime, port, assets[name])
        img = bpy.data.images.get(name)
        check(job.get("state") == "IMPORTED" and img is not None and img.packed_file is not None
              and img.use_fake_user, "image %s loaded, packed, kept" % name)

    # textures referenced from the received folder are packed before cleanup
    # (textures that were never sent, like the FBX's tex.png, are reported as warnings instead)
    unpacked = [(i.name, i.filepath) for i in bpy.data.images
                if i.packed_file is None and i.source == "FILE" and i.has_data
                and "uploads" in bpy.path.abspath(i.filepath)]
    check(not unpacked, "no received texture left pointing to the deleted upload folder %s" % unpacked)
    zip_textures = [i for i in bpy.data.images if i.packed_file is not None and i.name.startswith("tex")]
    check(bool(zip_textures), "textures inside obj_with_mtl.zip were packed into the .blend")

    # errors
    status, job = send_and_import(runtime, port, assets["broken.glb"])
    check(job.get("state") == "ERROR" and job.get("message"), "corrupt glb -> ERROR: %s" % job.get("message"))
    status, job = send_and_import(runtime, port, assets["broken.png"])
    check(job.get("state") == "ERROR", "corrupt png -> ERROR: %s" % job.get("message"))
    status, job = send_and_import(runtime, port, assets["lonely.gltf"])
    check(job.get("state") == "ERROR" and ".zip" in (job.get("message") or ""),
          "gltf without .bin -> ERROR with hint: %s" % job.get("message"))
    if ".dae" in unavailable:
        status, body = send_and_import(runtime, port, assets["old.dae"])
        check(status == 415, "unavailable importer rejected at upload (415): %s" % (body or {}).get("message"))

    # options: move to origin + no collection
    prefs.move_to_origin = True
    status, job = send_and_import(runtime, port, assets["model.obj"])
    coll = sorted((c for c in bpy.data.collections["Blender Drop"].children if c.name.startswith("model")),
                  key=lambda c: c.name)[-1]
    obj = coll.objects[0]
    bbox = [obj.matrix_world @ __import__("mathutils").Vector(c) for c in obj.bound_box]
    center = sum(bbox, __import__("mathutils").Vector()) / 8
    check(center.length < 1e-3, "move to origin centers the asset (center=%.4f)" % center.length)
    prefs.move_to_origin = False
    prefs.create_collection = False
    before = len(bpy.context.scene.collection.objects)
    status, job = send_and_import(runtime, port, assets["part.stl"])
    check(job.get("state") == "IMPORTED", "import without own collection works")
    prefs.create_collection = True

    # the user's existing objects were not touched
    for name, (loc, colls) in existing.items():
        o = bpy.data.objects.get(name)
        check(o is not None and tuple(o.location) == loc and
              tuple(c.name for c in o.users_collection) == colls, "existing object '%s' untouched" % name)

    # cleanup: job folders removed except those still referenced (alembic cache)
    left = os.listdir(prefs.upload_dir) if os.path.isdir(prefs.upload_dir) else []
    abc_refs = [c for c in bpy.data.cache_files]
    left_files = [os.path.relpath(os.path.join(r, f), prefs.upload_dir) for d in left
                  for r, _, fs in os.walk(os.path.join(prefs.upload_dir, d)) for f in fs]
    check(len(left) <= len(abc_refs), "upload folders cleaned (%d left, %d alembic caches) %s"
          % (len(left), len(abc_refs), left_files))
    if abc_refs:
        check(os.path.isfile(bpy.path.abspath(abc_refs[0].filepath)), "alembic cache file kept on disk")

    # history
    recent = runtime.state.store.recent(50)
    check(any(j["state"] == "ERROR" for j in recent) and any(j["state"] == "IMPORTED" for j in recent),
          "history keeps successes and failures")

    # stop
    bpy.ops.blender_drop.stop_server()
    check(not runtime.is_running(), "server stopped")
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        check(False, "port released after stop")
    except OSError:
        check(True, "port released after stop")
    runtime.process_pending()
    # restart with new token, then disable the add-on (must stop the server)
    bpy.ops.blender_drop.start_server()
    old_token_ok = runtime.state.token
    check(runtime.is_running(), "server restarts")
    addon_utils.disable("blender_drop", default_set=True)
    check(not runtime.is_running(), "disabling the add-on stops the server")
    check(not bpy.app.timers.is_registered(runtime._timer), "timer removed on disable")
    check(not hasattr(bpy.types, "VIEW3D_PT_blender_drop"), "classes unregistered")

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (len(PASSES), len(FAILURES)))
    for f in FAILURES:
        print("FAILED:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
