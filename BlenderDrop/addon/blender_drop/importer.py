"""Import handlers. MAIN THREAD ONLY (called from bpy.app.timers).

    extension -> handler -> real Blender operator / bpy.data API

Every operator name below was checked against Blender 5.2 LTS
(``bpy.ops.<module>.<name>.get_rna_type()``); several fall back to older
names for Blender 4.x. If no operator exists in the running Blender the
format is reported as unavailable instead of failing mysteriously.
"""

import os
import shutil
import zipfile

import bpy
from mathutils import Matrix, Vector

from . import utils

ROOT_COLLECTION_NAME = "Blender Drop"

# (operator path, extra kwargs). First available operator wins.
OPERATORS = {
    ".glb": [("import_scene.gltf", {})],
    ".gltf": [("import_scene.gltf", {})],
    # Python FBX importer (mature) first, Blender 5.x C++ importer as fallback.
    ".fbx": [("import_scene.fbx", {}), ("wm.fbx_import", {})],
    ".obj": [("wm.obj_import", {}), ("import_scene.obj", {})],
    ".stl": [("wm.stl_import", {}), ("import_mesh.stl", {})],
    ".ply": [("wm.ply_import", {}), ("import_mesh.ply", {})],
    # Collada was removed in Blender 5.0: only available on 4.x.
    ".dae": [("wm.collada_import", {})],
    ".abc": [("wm.alembic_import", {"as_background_job": False})],
    ".usd": [("wm.usd_import", {})],
    ".usda": [("wm.usd_import", {})],
    ".usdc": [("wm.usd_import", {})],
    ".usdz": [("wm.usd_import", {})],
    # X3D/VRML is an optional extension ("Web3D X3D/VRML2 format") since 4.2.
    ".x3d": [("import_scene.x3d", {})],
    ".wrl": [("import_scene.x3d", {})],
    ".bvh": [("import_anim.bvh", {})],
    ".svg": [("import_curve.svg", {}), ("wm.grease_pencil_import_svg", {}),
             ("wm.gpencil_import_svg", {})],
    ".vdb": [("object.volume_import", {})],
}

# Operators that read `directory` + `files` instead of (or as well as) filepath.
_NEEDS_FILES = {"object.volume_import", "wm.grease_pencil_import_svg",
                "wm.gpencil_import_svg", "image.import_as_mesh_planes"}

# Bundled ("core") add-ons that provide some operators. Enabled on demand
# for the current session only, never saved to the user's preferences.
_CORE_ADDONS = {
    "import_scene.gltf": "io_scene_gltf2",
    "import_scene.fbx": "io_scene_fbx",
    "import_curve.svg": "io_curve_svg",
    "import_anim.bvh": "io_anim_bvh",
}

# Files only kept as dependencies of another file (never imported alone from a zip).
_ZIP_IGNORED = {".bin", ".mtl"}
MAX_ZIP_FILES = 5000
MAX_ZIP_MODELS = 50


class ImportFailed(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ImportOptions:
    def __init__(self, create_collection=True, move_to_origin=False, images_as_planes=False,
                 max_unzipped_bytes=8 * 1024 ** 3):
        self.scope_dir = None  # folder whose images must be packed (the job folder)
        self.create_collection = create_collection
        self.move_to_origin = move_to_origin
        self.images_as_planes = images_as_planes
        self.max_unzipped_bytes = max_unzipped_bytes


class ImportResult:
    def __init__(self):
        self.objects = 0
        self.images = 0
        self.collection = ""
        self.keep_files = False
        self.warnings = []

    @property
    def summary(self):
        parts = []
        if self.objects:
            parts.append("%d object%s" % (self.objects, "" if self.objects == 1 else "s"))
        if self.images:
            parts.append("%d image%s" % (self.images, "" if self.images == 1 else "s"))
        text = ", ".join(parts) or "nothing new"
        if self.collection:
            text += " in '%s'" % self.collection
        return text


# --------------------------------------------------------------------------
# Operator discovery
# --------------------------------------------------------------------------

def _get_operator(path):
    module, name = path.split(".")
    try:
        op = getattr(getattr(bpy.ops, module), name)
        op.get_rna_type()          # raises KeyError when the operator does not exist
        return op
    except (AttributeError, KeyError):
        return None


_addon_module_names = None


def _core_addon_installed(module_name):
    global _addon_module_names
    if _addon_module_names is None:
        import addon_utils
        _addon_module_names = {mod.__name__ for mod in addon_utils.modules()}
    return module_name in _addon_module_names


def _resolve_operator(path, enable_addon):
    op = _get_operator(path)
    if op is not None:
        return op
    addon = _CORE_ADDONS.get(path)
    if addon and _core_addon_installed(addon):
        if not enable_addon:
            return True  # can be enabled when needed
        import addon_utils
        addon_utils.enable(addon, default_set=False)
        print("[Blender Drop] enabled bundled add-on '%s' for this session" % addon)
        return _get_operator(path)
    return None


def available_extensions():
    """Extensions this Blender can import right now (checked on the main thread)."""
    exts = set(utils.IMAGE_EXTENSIONS) | {".blend", ".zip"}
    for ext, candidates in OPERATORS.items():
        if any(_resolve_operator(path, enable_addon=False) for path, _ in candidates):
            exts.add(ext)
    return exts


def unavailable_extensions():
    return sorted(utils.ALL_EXTENSIONS - available_extensions())


# --------------------------------------------------------------------------
# Context helpers
# --------------------------------------------------------------------------

def _context_override():
    """Window/area for operators called from a timer (where context.window is None)."""
    wm = bpy.context.window_manager
    window = bpy.context.window
    if window is None and wm is not None and len(wm.windows):
        window = wm.windows[0]
    if window is None:
        return {}
    kwargs = {"window": window, "screen": window.screen}
    areas = [a for a in window.screen.areas if a.type == "VIEW_3D"]
    if areas:
        area = max(areas, key=lambda a: a.width * a.height)
        kwargs["area"] = area
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is not None:
            kwargs["region"] = region
    return kwargs


def _scene_and_view_layer(override):
    window = override.get("window")
    if window is not None:
        return window.scene, window.view_layer
    return bpy.context.scene, bpy.context.view_layer


def _ensure_object_mode():
    if bpy.context.mode != "OBJECT" and bpy.context.view_layer.objects.active is not None:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass


def _find_layer_collection(layer_collection, collection):
    if layer_collection.collection == collection:
        return layer_collection
    for child in layer_collection.children:
        found = _find_layer_collection(child, collection)
        if found is not None:
            return found
    return None


def _ensure_root_collection(scene):
    """'Blender Drop' collection of THIS scene (other scenes are never touched)."""
    for child in scene.collection.children:
        if child.library is None and child.get("blender_drop_root"):
            return child
    root = scene.collection.children.get(ROOT_COLLECTION_NAME)
    if root is None or root.library is not None:
        root = bpy.data.collections.new(ROOT_COLLECTION_NAME)
        scene.collection.children.link(root)
    root["blender_drop_root"] = True
    return root


def _make_visible(view_layer, collection):
    layer_coll = _find_layer_collection(view_layer.layer_collection, collection)
    if layer_coll is not None:
        layer_coll.exclude = False
        layer_coll.hide_viewport = False


def _subtree_pointers(collection):
    result = {collection.as_pointer()}
    for child in collection.children_recursive:
        result.add(child.as_pointer())
    return result


# --------------------------------------------------------------------------
# Snapshots (to find what an importer created)
# --------------------------------------------------------------------------

_TRACKED = ("objects", "collections", "images", "cache_files", "volumes",
            "libraries", "movieclips", "sounds")


def _snapshot():
    return {name: {item.as_pointer() for item in getattr(bpy.data, name)} for name in _TRACKED}


def _new_items(before, name):
    known = before[name]
    return [item for item in getattr(bpy.data, name) if item.as_pointer() not in known]


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def import_job(path, options, job_dir=None, display_name=None):
    """Import one received file into the current scene. Returns ImportResult."""
    if not os.path.isfile(path):
        raise ImportFailed("missing_file", "The received file disappeared before it could be imported.")
    result = ImportResult()
    options.scope_dir = job_dir or os.path.dirname(path)
    override = _context_override()
    with bpy.context.temp_override(**override):
        _ensure_object_mode()
        scene, view_layer = _scene_and_view_layer(override)
        name = display_name or utils.display_stem(path)
        if utils.get_extension(path) == ".zip":
            _import_zip(path, options, job_dir or os.path.dirname(path), name, scene, view_layer, result)
        else:
            _import_single(path, options, name, scene, view_layer, result, parent=None)
        if job_dir:
            _remove_unused_libraries(job_dir)
            result.keep_files = _directory_still_referenced(job_dir)
    return result


def _import_single(path, options, name, scene, view_layer, result, parent):
    ext = utils.get_extension(path)
    kind = utils.file_kind(path)
    if kind is None:
        raise ImportFailed("unsupported_type", "Unknown file type '%s'." % (ext or path))

    if kind == utils.KIND_IMAGE and not options.images_as_planes:
        _load_image(path, result)
        return
    if kind == utils.KIND_IMAGE and _get_operator("image.import_as_mesh_planes") is None:
        _load_image(path, result)
        result.warnings.append("Images as planes is not available in this Blender version.")
        return

    target = None
    if options.create_collection:
        root = parent or _ensure_root_collection(scene)
        target = bpy.data.collections.new(name)
        root.children.link(target)
        _make_visible(view_layer, root)
        _make_visible(view_layer, target)

    previous_active = view_layer.active_layer_collection.collection
    if target is not None:
        layer_coll = _find_layer_collection(view_layer.layer_collection, target)
        if layer_coll is not None:
            view_layer.active_layer_collection = layer_coll
    destination = target or view_layer.active_layer_collection.collection

    before = _snapshot()
    try:
        try:
            if ext == ".blend":
                _append_blend(path, destination)
            elif kind == utils.KIND_IMAGE:
                _run_operator("image.import_as_mesh_planes", path)
            else:
                _run_candidates(ext, path)
        finally:
            restored = _find_layer_collection(view_layer.layer_collection, previous_active)
            if restored is not None:
                view_layer.active_layer_collection = restored
    except Exception:
        if target is not None and not target.objects and not target.children:
            bpy.data.collections.remove(target)
        raise

    new_objects = _new_items(before, "objects")
    new_collections = _new_items(before, "collections")
    if target is not None:
        _relocate(scene, target, new_objects, new_collections, before)
    if options.move_to_origin and new_objects:
        _move_to_origin(view_layer, new_objects)

    new_images = _new_items(before, "images")
    missing = _pack_images(new_images, options.scope_dir or os.path.dirname(path))
    if missing:
        result.warnings.append("missing textures: %s (send a .zip with the model and its textures)"
                               % ", ".join(sorted(set(missing))[:5]))

    if not new_objects and kind != utils.KIND_IMAGE:
        if target is not None and not target.objects and not target.children:
            bpy.data.collections.remove(target)
        if new_images or _new_items(before, "cache_files"):
            result.images += len(new_images)
            return
        raise ImportFailed("empty_import", "The file was read, but it did not contain any object "
                           "Blender could add to the scene.")
    result.objects += len(new_objects)
    if target is not None and not result.collection:
        result.collection = target.name


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------

def _clean_error(exc):
    lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.startswith(("Traceback", "File \"", "^"))]
    text = lines[-1] if lines else exc.__class__.__name__
    for prefix in ("Error: ", "RuntimeError: "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text[:300]


def _run_candidates(ext, path):
    candidates = OPERATORS.get(ext)
    if not candidates:
        raise ImportFailed("unsupported_type", "Unknown file type '%s'." % ext)
    for op_path, extra in candidates:
        if _resolve_operator(op_path, enable_addon=True):
            return _run_operator(op_path, path, extra, ext)
    hint = ""
    if ext == ".dae":
        hint = " Collada (.dae) import was removed in Blender 5.0; convert it to .glb or .fbx."
    elif ext in (".x3d", ".wrl"):
        hint = " Install the 'Web3D X3D/VRML2' extension from Blender's extensions platform."
    raise ImportFailed("importer_missing",
                       "This Blender version has no importer for '%s' files.%s" % (ext, hint))


def _run_operator(op_path, path, extra=None, ext=None):
    op = _get_operator(op_path)
    if op is None:
        raise ImportFailed("importer_missing", "Importer '%s' is not available." % op_path)
    props = op.get_rna_type().properties.keys()
    kwargs = {"filepath": path}
    if op_path in _NEEDS_FILES:
        kwargs["directory"] = os.path.dirname(path) + os.sep
        kwargs["files"] = [{"name": os.path.basename(path)}]
    if "filepath" not in props:
        kwargs.pop("filepath")
    for key, value in (extra or {}).items():
        if key in props:
            kwargs[key] = value
    try:
        status = op("EXEC_DEFAULT", **kwargs)
    except RuntimeError as exc:
        message = _clean_error(exc)
        if ext == ".gltf":
            message += " (A .gltf with separate .bin/texture files must be sent as a .zip, or use .glb.)"
        raise ImportFailed("import_failed", "Blender could not import the file: " + message)
    if "FINISHED" not in status:
        hint = ""
        if ext == ".gltf":
            hint = " If the .gltf uses separate .bin/texture files, send a .zip with all of them or a .glb."
        raise ImportFailed("import_failed", "Blender could not import this file. It may be "
                           "damaged or use features the importer does not support." + hint)
    return status


# --------------------------------------------------------------------------
# Organisation
# --------------------------------------------------------------------------

def _relocate(scene, target, new_objects, new_collections, before):
    """Move whatever the importer created into `target`, never touching old data."""
    master = scene.collection
    master_ptr = master.as_pointer()
    old_collections = before["collections"]
    target_ptr = target.as_pointer()

    # 1) New collections linked at the top of the scene or under user collections.
    for coll in new_collections:
        if coll.as_pointer() == target_ptr:
            continue
        ours = _subtree_pointers(target)
        if coll.as_pointer() in ours:
            continue
        moved = False
        if master.children.get(coll.name) is not None and master.children.get(coll.name) == coll:
            master.children.unlink(coll)
            moved = True
        for parent in bpy.data.collections:
            if parent.as_pointer() in old_collections and parent.as_pointer() not in ours:
                if parent.children.get(coll.name) == coll:
                    parent.children.unlink(coll)
                    moved = True
        if moved and coll.as_pointer() not in _subtree_pointers(target):
            target.children.link(coll)

    # 2) New objects linked to the scene collection or to pre-existing collections.
    ours = _subtree_pointers(target)
    for obj in new_objects:
        users = list(obj.users_collection)
        if not users:
            continue
        in_ours = False
        for coll in users:
            ptr = coll.as_pointer()
            if ptr in ours:
                in_ours = True
            elif ptr == master_ptr or ptr in old_collections:
                coll.objects.unlink(obj)
        if not in_ours:
            target.objects.link(obj)


_BOUNDED_TYPES = {"MESH", "CURVE", "SURFACE", "FONT", "META", "VOLUME", "CURVES",
                  "POINTCLOUD", "GPENCIL", "GREASEPENCIL", "LATTICE", "ARMATURE"}


def _move_to_origin(view_layer, objects):
    view_layer.update()
    pointers = {o.as_pointer() for o in objects}
    points = []
    for obj in objects:
        if obj.type in _BOUNDED_TYPES:
            matrix = obj.matrix_world
            points.extend(matrix @ Vector(corner) for corner in obj.bound_box)
    if not points:
        points = [o.matrix_world.translation.copy() for o in objects]
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    offset = Matrix.Translation(-(lo + hi) / 2.0)
    for obj in objects:
        if obj.parent is None or obj.parent.as_pointer() not in pointers:
            obj.matrix_world = offset @ obj.matrix_world


# --------------------------------------------------------------------------
# .blend
# --------------------------------------------------------------------------

def _append_blend(path, destination):
    """APPEND (never open) the content of a .blend into `destination`.

    The first scene of the file is appended temporarily so its collection
    hierarchy is preserved, its content is linked under `destination`, then
    the temporary scene is removed. The current scene is never replaced.
    """
    try:
        with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
            scene_names = list(data_from.scenes)
            if scene_names:
                data_to.scenes = [scene_names[0]]
            else:
                data_to.collections = list(data_from.collections)
                data_to.objects = list(data_from.objects)
    except OSError as exc:
        raise ImportFailed("import_failed", "This .blend file could not be read (%s)." % _clean_error(exc))

    scenes = [s for s in getattr(data_to, "scenes", []) if s is not None]
    if scenes:
        temp_scene = scenes[0]
        for child in list(temp_scene.collection.children):
            if destination.children.get(child.name) is None:
                destination.children.link(child)
        for obj in list(temp_scene.collection.objects):
            if destination.objects.get(obj.name) is None:
                destination.objects.link(obj)
        bpy.data.scenes.remove(temp_scene)
        return

    collections = [c for c in getattr(data_to, "collections", []) if c is not None]
    child_ptrs = set()
    for coll in collections:
        child_ptrs.update(c.as_pointer() for c in coll.children)
    in_collections = set()
    for coll in collections:
        if coll.as_pointer() not in child_ptrs:
            destination.children.link(coll)
        in_collections.update(o.as_pointer() for o in coll.all_objects)
    for obj in getattr(data_to, "objects", []):
        if obj is not None and obj.as_pointer() not in in_collections and not obj.users_collection:
            destination.objects.link(obj)


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def _load_image(path, result):
    try:
        image = bpy.data.images.load(path, check_existing=False)
    except RuntimeError as exc:
        raise ImportFailed("import_failed", "Blender could not read this image: " + _clean_error(exc))
    width, height = image.size[:]
    if not width or not height:
        bpy.data.images.remove(image)
        raise ImportFailed("import_failed", "The image is damaged or its format is not supported.")
    image.name = os.path.basename(path)
    image.use_fake_user = True        # keep it when the .blend is saved, even if unused
    try:
        image.pack()                  # the received file is temporary
    except RuntimeError:
        result.warnings.append("Image could not be packed; the received file was kept.")
    result.images += 1


def _pack_images(images, directory):
    """Pack textures that live in the (temporary) job folder. Returns missing names."""
    missing = []
    for image in images:
        if image.packed_file is not None or image.source not in {"FILE", "TILED"}:
            continue
        filepath = bpy.path.abspath(image.filepath, library=image.library)
        if not filepath:
            continue
        if not os.path.isfile(filepath):
            missing.append(os.path.basename(filepath))
            continue
        if not utils.is_path_inside(filepath, directory):
            continue
        try:
            image.pack()
        except RuntimeError:
            pass
    return missing


def _library_in_use(library):
    for attr in dir(bpy.data):
        if attr == "libraries" or attr.startswith("_"):
            continue
        collection = getattr(bpy.data, attr, None)
        if not isinstance(collection, bpy.types.bpy_prop_collection):
            continue
        for item in collection:
            if isinstance(item, bpy.types.ID) and item.library == library:
                return True
    return False


def _remove_unused_libraries(directory):
    """After an APPEND, Blender may keep an unused Library entry pointing to the
    received .blend; remove it so the temporary file can be deleted."""
    for library in list(bpy.data.libraries):
        path = bpy.path.abspath(library.filepath)
        if utils.is_path_inside(path, directory) and not _library_in_use(library):
            bpy.data.libraries.remove(library)


def _directory_still_referenced(directory):
    """True if Blender still reads files from `directory` (caches, volumes...)."""
    for collection_name in ("images", "cache_files", "volumes", "movieclips", "sounds", "libraries"):
        for item in getattr(bpy.data, collection_name):
            if getattr(item, "packed_file", None) is not None:
                continue
            filepath = getattr(item, "filepath", "")
            if not filepath:
                continue
            absolute = bpy.path.abspath(filepath, library=getattr(item, "library", None))
            if utils.is_path_inside(absolute, directory) and os.path.exists(absolute):
                return True
    return False


# --------------------------------------------------------------------------
# .zip (e.g. .gltf + .bin + textures, .obj + .mtl + textures)
# --------------------------------------------------------------------------

def _safe_extract(zip_path, destination, max_bytes):
    try:
        free = shutil.disk_usage(destination).free - 200 * 1024 * 1024
        max_bytes = max(0, min(max_bytes, free))
    except OSError:
        pass
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ImportFailed("import_failed", "The .zip file is damaged (%s)." % exc)
    extracted = []
    total = 0
    with archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_FILES:
            raise ImportFailed("import_failed", "The .zip contains too many files.")
        for info in members:
            name = info.filename.replace("\\", "/")
            if info.is_dir() or name.startswith("__MACOSX/") or "/._" in "/" + name:
                continue
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if not parts or any(p == ".." for p in parts) or ":" in parts[0] or name.startswith("/"):
                continue  # unsafe path (zip-slip): skip
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue  # symlink
            total += info.file_size
            if total > max_bytes:
                raise ImportFailed("import_failed", "The .zip is too large once uncompressed.")
            target = os.path.join(destination, *parts)
            if not utils.is_path_inside(target, destination):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                while True:
                    block = src.read(1024 * 1024)
                    if not block:
                        break
                    dst.write(block)
            extracted.append(target)
    return extracted


def _import_zip(zip_path, options, job_dir, name, scene, view_layer, result):
    destination = os.path.join(job_dir, "zip_contents")
    os.makedirs(destination, exist_ok=True)
    files = _safe_extract(zip_path, destination, options.max_unzipped_bytes)
    models = sorted(
        (f for f in files
         if utils.file_kind(f) in (utils.KIND_MODEL, utils.KIND_VECTOR)
         and utils.get_extension(f) not in _ZIP_IGNORED),
        key=lambda f: (f.count(os.sep), f.lower()))
    if not models:
        images = [f for f in files if utils.file_kind(f) == utils.KIND_IMAGE]
        if not images:
            raise ImportFailed("unsupported_type", "The .zip does not contain any file Blender Drop can import.")
        for image in images[:MAX_ZIP_MODELS]:
            _load_image(image, result)
        return
    if len(models) > MAX_ZIP_MODELS:
        raise ImportFailed("import_failed", "The .zip contains more than %d models." % MAX_ZIP_MODELS)

    if len(models) == 1:
        _import_single(models[0], options, name, scene, view_layer, result, parent=None)
        return
    parent = None
    if options.create_collection:
        parent = bpy.data.collections.new(name)
        _ensure_root_collection(scene).children.link(parent)
        _make_visible(view_layer, parent)
        result.collection = parent.name
    errors = []
    for model in models:
        try:
            _import_single(model, options, utils.display_stem(model), scene, view_layer, result, parent)
        except ImportFailed as exc:
            errors.append("%s: %s" % (os.path.basename(model), exc.message))
    if errors and not result.objects:
        raise ImportFailed("import_failed", "; ".join(errors)[:500])
    result.warnings.extend(errors)
