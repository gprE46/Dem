"""Glue between the HTTP server (background threads) and Blender (main thread).

    HTTP thread  -> save file -> queue.put(job_id) -> HTTP 202
    bpy.app.timers (main thread) -> queue.get_nowait() -> importer -> job state

Only this module and importer.py/ui.py use bpy, and only from the main thread.
"""

import errno
import glob
import os
import queue
import secrets
import shutil
import time
import traceback

import bpy

from . import importer, jobs, qr, server, utils

ADDON_ID = __package__
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MB = 1024 * 1024
TIMER_BUSY = 0.05
TIMER_IDLE = 0.25


class _State:
    def __init__(self):
        self.server = None
        self.token = ""
        self.hosts = []
        self.host = ""
        self.error = ""
        self.store = jobs.JobStore(max_jobs=50)
        self.queue = jobs.new_queue()
        self.pending_job = None
        self.redraw_version = -1
        self.timer_on = False
        self.previews = None
        self.qr_key = ""
        self.qr_path = ""
        self.last_job_id = ""
        self.unavailable = []


state = _State()


# --------------------------------------------------------------------------
# Preferences / paths
# --------------------------------------------------------------------------

def prefs():
    addon = bpy.context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def data_dir():
    """Writable folder owned by the add-on (never inside the add-on itself)."""
    path = None
    if hasattr(bpy.utils, "extension_path_user"):
        try:
            path = bpy.utils.extension_path_user(ADDON_ID, create=True)
        except (ValueError, KeyError, RuntimeError):
            path = None   # installed as a legacy add-on
    if not path:
        path = bpy.utils.user_resource("DATAFILES", path="blender_drop", create=True)
    os.makedirs(path, exist_ok=True)
    return path


def upload_dir():
    p = prefs()
    custom = bpy.path.abspath(p.upload_dir).strip() if p and p.upload_dir else ""
    return custom if custom else os.path.join(data_dir(), "uploads")


# --------------------------------------------------------------------------
# Server lifecycle
# --------------------------------------------------------------------------

def is_running():
    return state.server is not None and state.server.running


def server_url(with_token=True):
    if not is_running():
        return ""
    return utils.build_url(state.host, state.server.port, state.token if with_token else None)


def _friendly_os_error(exc, port):
    code = exc.errno
    if code in (errno.EADDRINUSE, 10048):
        return "Port %d is already in use. Choose another port or close the other program." % port
    if code in (errno.EACCES, 10013):
        return "Permission denied for port %d. Try a port above 1024." % port
    return "Could not start the server: %s" % (exc.strerror or exc)


def _cleanup_stale(directory):
    """Remove leftovers of interrupted uploads (folders with *.part files)."""
    for part in glob.glob(os.path.join(directory, "*", "*.part")):
        try:
            if time.time() - os.path.getmtime(part) > 3600:
                shutil.rmtree(os.path.dirname(part), ignore_errors=True)
        except OSError:
            pass


def start_server():
    if is_running():
        return True
    p = prefs()
    port = p.port if p else 8765
    state.error = ""
    try:
        directory = upload_dir()
        os.makedirs(directory, exist_ok=True)
        _cleanup_stale(directory)
    except OSError as exc:
        state.error = "Cannot use the upload folder: %s" % (exc.strerror or exc)
        return False

    allowed = importer.available_extensions()
    state.unavailable = sorted(utils.ALL_EXTENSIONS - allowed)
    token = secrets.token_urlsafe(16)
    config = server.ServerConfig(
        port=port, token=token, upload_dir=directory, web_dir=WEB_DIR,
        max_bytes=(p.max_upload_mb if p else 2048) * MB,
        allowed_extensions=allowed,
        info={"blender": bpy.app.version_string, "scene": _scene_name()},
        verbose=bool(p and p.verbose_log),
    )
    srv = server.DropServer(config, state.store, state.queue)
    try:
        srv.start()
    except OSError as exc:
        state.error = _friendly_os_error(exc, port)
        print("[Blender Drop] " + state.error)
        return False

    state.server = srv
    state.token = token
    refresh_hosts()
    ensure_timer()
    url = server_url()
    print("[Blender Drop] server running: " + url)
    if state.unavailable:
        print("[Blender Drop] formats without importer in this Blender: " + ", ".join(state.unavailable))
    try:
        print(qr.QrCode(url).to_text())
    except Exception:
        pass
    tag_redraw()
    return True


def stop_server():
    srv = state.server
    if srv is None:
        return
    state.server = None
    try:
        srv.stop()
    except Exception:
        traceback.print_exc()
    state.token = ""
    _remove_qr_files()
    print("[Blender Drop] server stopped")
    tag_redraw()


def refresh_hosts():
    hosts = utils.get_lan_ips()
    previous = state.host
    state.hosts = hosts
    state.host = previous if previous in hosts else hosts[0]
    make_qr()


def set_host(host):
    if host and host != state.host:
        state.host = host
        make_qr()
        tag_redraw()


def _scene_name():
    try:
        return bpy.context.scene.name
    except AttributeError:
        return ""


# --------------------------------------------------------------------------
# QR code (pure-Python PNG shown through a preview collection)
# --------------------------------------------------------------------------

def _remove_qr_files():
    try:
        for path in glob.glob(os.path.join(data_dir(), "qr_*.png")):
            os.remove(path)
    except OSError:
        pass
    state.qr_key = ""
    state.qr_path = ""


def make_qr():
    url = server_url()
    if not url:
        return
    _remove_qr_files()
    key = "qr_%s_%s" % (secrets.token_hex(4), state.host.replace(".", "_"))
    path = os.path.join(data_dir(), key + ".png")
    try:
        qr.write_png(url, path, scale=10, border=4)
    except Exception:
        traceback.print_exc()
        return
    state.qr_path = path
    if state.previews is not None:
        try:
            state.previews.load(key, path, "IMAGE")
            state.qr_key = key
        except (KeyError, RuntimeError):
            traceback.print_exc()


def qr_icon_id():
    if state.previews is None or not state.qr_key or state.qr_key not in state.previews:
        return 0
    preview = state.previews[state.qr_key]
    preview.image_size[:]  # forces the image preview to load
    return preview.icon_id


# --------------------------------------------------------------------------
# Main-thread queue processing
# --------------------------------------------------------------------------

def ensure_timer():
    if not state.timer_on:
        bpy.app.timers.register(_timer, first_interval=TIMER_IDLE, persistent=True)
        state.timer_on = True


def remove_timer():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    state.timer_on = False


def _timer():
    try:
        busy = process_pending()
    except Exception:
        traceback.print_exc()
        busy = False
    if state.server is None and state.pending_job is None and state.queue.empty():
        state.timer_on = False
        tag_redraw()
        return None             # unregisters the timer
    return TIMER_BUSY if busy else TIMER_IDLE


def process_pending():
    """One step of the import pipeline. Returns True while there is work.

    Two steps per job: first mark it IMPORTING (so the UI and the phone can
    show it), then import on the next tick.
    """
    busy = False
    if state.pending_job is not None:
        job_id, state.pending_job = state.pending_job, None
        _import_job(job_id)
        busy = not state.queue.empty()
    else:
        try:
            job_id = state.queue.get_nowait()
        except queue.Empty:
            job_id = None
        if job_id is not None:
            state.store.update(job_id, state=jobs.IMPORTING, message="Importing...")
            state.pending_job = job_id
            busy = True
    _update_server_info()
    if state.store.version != state.redraw_version:
        state.redraw_version = state.store.version
        tag_redraw()
    return busy


def _update_server_info():
    srv = state.server
    if srv is None:
        return
    name = _scene_name()
    if srv.config.info.get("scene") != name:
        # Replace the dict (atomic reference swap, safe for HTTP threads).
        srv.config.info = dict(srv.config.info, scene=name)


def _import_job(job_id):
    job = state.store.get_job(job_id)
    if job is None:
        return
    state.last_job_id = job_id
    p = prefs()
    options = importer.ImportOptions(
        create_collection=p.create_collection if p else True,
        move_to_origin=p.move_to_origin if p else False,
        images_as_planes=p.images_as_planes if p else False,
    )
    keep = bool(p and p.keep_files)
    started = time.time()
    try:
        result = importer.import_job(job.path, options, job_dir=job.directory,
                                     display_name=utils.display_stem(job.filename))
    except importer.ImportFailed as exc:
        state.store.update(job_id, state=jobs.ERROR, error_code=exc.code, message=exc.message)
        print("[Blender Drop] import failed: %s: %s" % (job.filename, exc.message))
    except Exception as exc:  # any Blender/importer bug must not stop the add-on
        traceback.print_exc()
        state.store.update(job_id, state=jobs.ERROR, error_code="import_failed",
                           message="Blender error while importing: %s" % importer._clean_error(exc))
    else:
        keep = keep or result.keep_files
        details = result.summary
        if result.warnings:
            details += " (" + "; ".join(result.warnings)[:200] + ")"
        state.store.update(job_id, state=jobs.IMPORTED, message="Imported successfully",
                           details=details)
        print("[Blender Drop] imported %s in %.1fs: %s" % (job.filename, time.time() - started, details))
        _undo_push("Blender Drop: import %s" % job.filename)
    finally:
        if not keep and job.directory:
            shutil.rmtree(job.directory, ignore_errors=True)


def _undo_push(message):
    try:
        with bpy.context.temp_override(**importer._context_override()):
            bpy.ops.ed.undo_push(message=message)
    except Exception:
        pass


def tag_redraw():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "OUTLINER", "PREFERENCES"}:
                area.tag_redraw()


# --------------------------------------------------------------------------
# Register helpers
# --------------------------------------------------------------------------

def startup():
    import bpy.utils.previews
    if state.previews is None:
        state.previews = bpy.utils.previews.new()


def shutdown():
    """Add-on disabled or Blender closing: stop everything, drop pending files."""
    stop_server()
    remove_timer()
    pending = [state.pending_job] if state.pending_job else []
    state.pending_job = None
    while True:
        try:
            pending.append(state.queue.get_nowait())
        except queue.Empty:
            break
    for job_id in pending:
        job = state.store.get_job(job_id)
        state.store.update(job_id, state=jobs.ERROR, error_code="server_stopped",
                           message="Blender Drop was disabled before importing this file.")
        if job is not None and job.directory:
            shutil.rmtree(job.directory, ignore_errors=True)
    if state.previews is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(state.previews)
        state.previews = None
