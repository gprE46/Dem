"""Blender UI: preferences, operators and the "Blender Drop" sidebar panel."""

import os
import textwrap

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import jobs, runtime, utils

ADDON_ID = __package__


def _available_icons():
    try:
        return set(bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys())
    except (KeyError, AttributeError):
        return set()


_ICONS = None


def icon(*names):
    """First icon that exists in this Blender version (names changed between 4.x and 5.x)."""
    global _ICONS
    if _ICONS is None:
        _ICONS = _available_icons()
    for name in names:
        if name in _ICONS:
            return name
    return "NONE"


def _wrapped(layout, text, width=38, icon_name="NONE"):
    lines = textwrap.wrap(text, width) or [""]
    col = layout.column(align=True)
    for i, line in enumerate(lines):
        col.label(text=line, icon=icon_name if i == 0 else "BLANK1")


# --------------------------------------------------------------------------
# Preferences
# --------------------------------------------------------------------------

class BlenderDropPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    port: IntProperty(name="Port", default=8765, min=1024, max=65535,
                      description="TCP port of the local server (restart the server to apply)")
    max_upload_mb: IntProperty(name="Max upload (MB)", default=2048, min=1, max=65536,
                               description="Largest file accepted from the phone")
    upload_dir: StringProperty(name="Upload folder", subtype="DIR_PATH", default="",
                               description="Where received files are stored temporarily. "
                                           "Empty = Blender Drop's own data folder")
    keep_files: BoolProperty(name="Keep received files", default=False,
                             description="Do not delete received files after importing them")
    auto_start: BoolProperty(name="Start server when Blender starts", default=False)
    verbose_log: BoolProperty(name="Log every request to the console", default=False)

    create_collection: BoolProperty(
        name="Create collection", default=True,
        description="Put every import in its own collection inside 'Blender Drop'")
    move_to_origin: BoolProperty(
        name="Move imported asset to origin", default=False,
        description="Center the imported objects on the world origin (default keeps the file's coordinates)")
    images_as_planes: BoolProperty(
        name="Images as planes", default=False,
        description="Create a textured plane for received images instead of only loading them")

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.prop(self, "port")
        col.prop(self, "max_upload_mb")
        col.prop(self, "upload_dir")
        col.prop(self, "keep_files")
        col.prop(self, "auto_start")
        col.prop(self, "verbose_log")
        box = layout.box()
        box.label(text="Import options")
        box.prop(self, "create_collection")
        box.prop(self, "move_to_origin")
        box.prop(self, "images_as_planes")
        layout.operator(BLENDERDROP_OT_open_folder.bl_idname, icon=icon("FILE_FOLDER"))


# --------------------------------------------------------------------------
# Window-manager properties (session only)
# --------------------------------------------------------------------------

_host_items = []


def _host_enum_items(self, context):
    global _host_items
    hosts = runtime.state.hosts or ["127.0.0.1"]
    _host_items = [(h, h, "Use this network address for the URL and the QR") for h in hosts]
    return _host_items


def _host_update(self, context):
    runtime.set_host(self.blender_drop_host)


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------

class BLENDERDROP_OT_start(bpy.types.Operator):
    bl_idname = "blender_drop.start_server"
    bl_label = "Start Server"
    bl_description = "Start the local Wi-Fi server that receives files from your phone"

    def execute(self, context):
        if runtime.start_server():
            self.report({"INFO"}, "Blender Drop running at " + runtime.server_url(with_token=False))
            return {"FINISHED"}
        self.report({"ERROR"}, runtime.state.error or "Could not start the server")
        return {"CANCELLED"}


class BLENDERDROP_OT_stop(bpy.types.Operator):
    bl_idname = "blender_drop.stop_server"
    bl_label = "Stop Server"
    bl_description = "Stop the local server (files already received are still imported)"

    def execute(self, context):
        runtime.stop_server()
        context.window_manager.blender_drop_show_qr = False
        return {"FINISHED"}


class BLENDERDROP_OT_copy_url(bpy.types.Operator):
    bl_idname = "blender_drop.copy_url"
    bl_label = "Copy URL"
    bl_description = "Copy the connection URL (including the session token) to the clipboard"

    @classmethod
    def poll(cls, context):
        return runtime.is_running()

    def execute(self, context):
        context.window_manager.clipboard = runtime.server_url()
        self.report({"INFO"}, "URL copied to the clipboard")
        return {"FINISHED"}


class BLENDERDROP_OT_toggle_qr(bpy.types.Operator):
    bl_idname = "blender_drop.toggle_qr"
    bl_label = "Show QR"
    bl_description = "Show a QR code to open Blender Drop with the phone camera"

    @classmethod
    def poll(cls, context):
        return runtime.is_running()

    def execute(self, context):
        wm = context.window_manager
        wm.blender_drop_show_qr = not wm.blender_drop_show_qr
        if wm.blender_drop_show_qr and not runtime.state.qr_path:
            runtime.make_qr()
        return {"FINISHED"}


class BLENDERDROP_OT_open_qr(bpy.types.Operator):
    bl_idname = "blender_drop.open_qr_image"
    bl_label = "Open QR Image"
    bl_description = "Open the QR code as an image with your system viewer (bigger)"

    @classmethod
    def poll(cls, context):
        return runtime.is_running()

    def execute(self, context):
        if not runtime.state.qr_path or not os.path.isfile(runtime.state.qr_path):
            runtime.make_qr()
        if not runtime.state.qr_path:
            self.report({"ERROR"}, "Could not create the QR image")
            return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=runtime.state.qr_path)
        return {"FINISHED"}


class BLENDERDROP_OT_refresh_ips(bpy.types.Operator):
    bl_idname = "blender_drop.refresh_addresses"
    bl_label = "Refresh Addresses"
    bl_description = "Detect the computer's local network addresses again (after changing Wi-Fi)"

    def execute(self, context):
        runtime.refresh_hosts()
        return {"FINISHED"}


class BLENDERDROP_OT_open_folder(bpy.types.Operator):
    bl_idname = "blender_drop.open_upload_folder"
    bl_label = "Open Upload Folder"
    bl_description = "Open the folder where received files are stored temporarily"

    def execute(self, context):
        path = runtime.upload_dir()
        os.makedirs(path, exist_ok=True)
        bpy.ops.wm.path_open(filepath=path)
        return {"FINISHED"}


class BLENDERDROP_OT_clear_history(bpy.types.Operator):
    bl_idname = "blender_drop.clear_history"
    bl_label = "Clear History"
    bl_description = "Clear the list of received files (does not touch the scene)"

    def execute(self, context):
        runtime.state.store.clear()
        runtime.state.last_job_id = ""
        return {"FINISHED"}


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------

_STATE_LABELS = {
    jobs.UPLOADING: "Uploading...",
    jobs.UPLOADED: "Uploaded",
    jobs.QUEUED: "Queued",
    jobs.IMPORTING: "Importing...",
    jobs.IMPORTED: "Imported successfully",
    jobs.ERROR: "Import failed",
}


def _state_icon(state):
    if state == jobs.IMPORTED:
        return icon("CHECKMARK")
    if state == jobs.ERROR:
        return icon("ERROR")
    return icon("SORTTIME", "TIME")


class VIEW3D_PT_blender_drop(bpy.types.Panel):
    bl_label = "Blender Drop"
    bl_idname = "VIEW3D_PT_blender_drop"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blender Drop"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        running = runtime.is_running()

        row = layout.row()
        row.label(text="Status:")
        if running:
            row.label(text="Running", icon=icon("STRIP_COLOR_04", "SEQUENCE_COLOR_04", "CHECKMARK"))
        else:
            row.label(text="Stopped", icon=icon("STRIP_COLOR_01", "SEQUENCE_COLOR_01", "CANCEL"))

        if running:
            box = layout.box()
            box.label(text="Server URL:")
            box.label(text=runtime.server_url(with_token=False), icon=icon("URL"))
            if len(runtime.state.hosts) > 1:
                box.prop(wm, "blender_drop_host", text="Address")
            layout.operator(BLENDERDROP_OT_stop.bl_idname, icon=icon("PAUSE"))
            row = layout.row(align=True)
            row.operator(BLENDERDROP_OT_copy_url.bl_idname, icon=icon("COPYDOWN"))
            row.operator(BLENDERDROP_OT_toggle_qr.bl_idname,
                         text="Hide QR" if wm.blender_drop_show_qr else "Show QR",
                         icon=icon("HIDE_OFF"))
            if wm.blender_drop_show_qr:
                qr_box = layout.box()
                icon_id = runtime.qr_icon_id()
                if icon_id:
                    qr_box.template_icon(icon_value=icon_id, scale=9.0)
                else:
                    qr_box.label(text="QR preview not available", icon=icon("INFO"))
                qr_box.label(text="Scan with the iPhone camera")
                qr_box.operator(BLENDERDROP_OT_open_qr.bl_idname, icon=icon("IMAGE_DATA"))
        else:
            prefs = runtime.prefs()
            if prefs is not None:
                layout.prop(prefs, "port")
            layout.operator(BLENDERDROP_OT_start.bl_idname, icon=icon("PLAY"))
            if runtime.state.error:
                _wrapped(layout.box(), runtime.state.error, icon_name=icon("ERROR"))

        last = runtime.state.store.get(runtime.state.last_job_id) if runtime.state.last_job_id else None
        if last is None:
            recent = runtime.state.store.recent(1)
            last = recent[0] if recent else None
        box = layout.box()
        box.label(text="Last received file:")
        if last is None:
            box.label(text="Nothing received yet", icon=icon("INFO"))
        else:
            box.label(text=last["filename"], icon=icon("FILE_BLEND", "FILE"))
            box.label(text="Status:")
            text = last["message"] if last["state"] in (jobs.UPLOADING, jobs.ERROR) else \
                _STATE_LABELS.get(last["state"], last["state"])
            _wrapped(box, text, icon_name=_state_icon(last["state"]))
            if last["state"] == jobs.IMPORTED and last.get("details"):
                _wrapped(box, last["details"], icon_name="BLANK1")


class VIEW3D_PT_blender_drop_options(bpy.types.Panel):
    bl_label = "Import Options"
    bl_idname = "VIEW3D_PT_blender_drop_options"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blender Drop"
    bl_parent_id = "VIEW3D_PT_blender_drop"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        prefs = runtime.prefs()
        if prefs is None:
            return
        layout.prop(prefs, "create_collection")
        layout.prop(prefs, "move_to_origin")
        layout.prop(prefs, "images_as_planes")
        layout.prop(prefs, "max_upload_mb")
        if runtime.state.unavailable:
            _wrapped(layout, "Not available in this Blender: " + ", ".join(runtime.state.unavailable),
                     icon_name=icon("INFO"))


class VIEW3D_PT_blender_drop_history(bpy.types.Panel):
    bl_label = "Recent Files"
    bl_idname = "VIEW3D_PT_blender_drop_history"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blender Drop"
    bl_parent_id = "VIEW3D_PT_blender_drop"

    def draw(self, context):
        layout = self.layout
        recent = runtime.state.store.recent(10)
        if not recent:
            layout.label(text="No files yet")
            return
        col = layout.column(align=True)
        for job in recent:
            row = col.row()
            row.label(text=job["filename"], icon=_state_icon(job["state"]))
            if job["state"] == jobs.ERROR:
                row.label(text="Failed")
            elif job["state"] == jobs.IMPORTED:
                row.label(text=utils.human_size(job["size"]))
            else:
                row.label(text=_STATE_LABELS.get(job["state"], job["state"]))
        row = layout.row(align=True)
        row.operator(BLENDERDROP_OT_clear_history.bl_idname, icon=icon("TRASH", "X"))
        row.operator(BLENDERDROP_OT_refresh_ips.bl_idname, text="", icon=icon("FILE_REFRESH"))


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

CLASSES = (
    BlenderDropPreferences,
    BLENDERDROP_OT_start,
    BLENDERDROP_OT_stop,
    BLENDERDROP_OT_copy_url,
    BLENDERDROP_OT_toggle_qr,
    BLENDERDROP_OT_open_qr,
    BLENDERDROP_OT_refresh_ips,
    BLENDERDROP_OT_open_folder,
    BLENDERDROP_OT_clear_history,
    VIEW3D_PT_blender_drop,
    VIEW3D_PT_blender_drop_options,
    VIEW3D_PT_blender_drop_history,
)


def _auto_start():
    prefs = runtime.prefs()
    if prefs is not None and prefs.auto_start and not runtime.is_running():
        runtime.start_server()
    return None


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.blender_drop_show_qr = BoolProperty(name="Show QR", default=False)
    bpy.types.WindowManager.blender_drop_host = EnumProperty(
        name="Address", items=_host_enum_items, update=_host_update,
        description="Network address of this computer used in the URL")
    runtime.startup()
    prefs = runtime.prefs()
    if prefs is not None and prefs.auto_start:
        bpy.app.timers.register(_auto_start, first_interval=1.0)


def unregister():
    runtime.shutdown()
    if bpy.app.timers.is_registered(_auto_start):
        bpy.app.timers.unregister(_auto_start)
    del bpy.types.WindowManager.blender_drop_host
    del bpy.types.WindowManager.blender_drop_show_qr
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
