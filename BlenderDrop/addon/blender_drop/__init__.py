# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender Drop: send assets from your phone into the current Blender scene over local Wi-Fi.

Installable both as a Blender 4.2+ extension (blender_manifest.toml) and as a
legacy add-on (bl_info). Sub-modules that need bpy are imported lazily so the
pure-Python parts (server, multipart, qr, utils, jobs) can be unit-tested
without Blender.
"""

bl_info = {
    "name": "Blender Drop",
    "author": "Blender Drop contributors",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Blender Drop",
    "description": "Send assets from your phone into the current scene over local Wi-Fi",
    "doc_url": "",
    "category": "Import-Export",
}


def register():
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
