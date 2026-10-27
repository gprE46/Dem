"""Draw the add-on panels in every state against Blender's real UILayout API.

Background Blender does not draw UI, so a recording layout validates each call
(method names, keyword arguments, icon names, properties and operator ids)
against bpy.types.UILayout.bl_rna. Catches typos that would only show up as
errors when the panel is drawn in the GUI.

    python tests/blender_ui_draw_test.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "addon"))
import bpy  # noqa: E402
import addon_utils  # noqa: E402

RNA = bpy.types.UILayout.bl_rna.functions
ICONS = set(RNA["prop"].parameters["icon"].enum_items.keys())
errors = []
calls = []


class FakeLayout:
    def __getattr__(self, name):
        if name not in RNA:
            errors.append("UILayout has no method '%s'" % name)

        def method(*args, **kwargs):
            calls.append(name)
            params = RNA[name].parameters if name in RNA else {}
            for key, value in kwargs.items():
                if key not in params:
                    errors.append("%s(): unknown argument '%s'" % (name, key))
                if key == "icon" and value not in ICONS:
                    errors.append("%s(): unknown icon '%s'" % (name, value))
            if name == "prop":
                data, prop = args[0], args[1]
                if prop not in data.bl_rna.properties:
                    errors.append("prop(): '%s' has no property '%s'" % (data, prop))
            if name == "operator":
                mod, op = args[0].split(".")
                try:
                    getattr(getattr(bpy.ops, mod), op).get_rna_type()
                except KeyError:
                    errors.append("operator(): unknown operator '%s'" % args[0])
            if name == "template_icon" and not kwargs.get("icon_value"):
                errors.append("template_icon() without icon_value")
            return FakeLayout()
        return method


class FakePanel:
    layout = None

    def __init__(self, target=None):
        self._target = target

    def __getattr__(self, name):          # preferences draw() uses self.<prop>
        return getattr(self._target, name)


def draw_all(label):
    from blender_drop import ui
    for panel in (ui.VIEW3D_PT_blender_drop, ui.VIEW3D_PT_blender_drop_options,
                  ui.VIEW3D_PT_blender_drop_history):
        fake = FakePanel()
        fake.layout = FakeLayout()
        try:
            panel.draw(fake, bpy.context)
        except Exception as exc:  # noqa: BLE001
            errors.append("%s draw failed (%s): %r" % (panel.__name__, label, exc))
    prefs = bpy.context.preferences.addons["blender_drop"].preferences
    fake = FakePanel(prefs)
    fake.layout = FakeLayout()
    type(prefs).draw(fake, bpy.context)


bpy.ops.wm.read_homefile(use_factory_startup=True)
addon_utils.enable("blender_drop", default_set=True)
from blender_drop import jobs, runtime  # noqa: E402
runtime.prefs().port = 18767
runtime.prefs().upload_dir = tempfile.mkdtemp()

draw_all("stopped, empty")
runtime.state.error = "Port 8765 is already in use. Choose another port or close the other program."
draw_all("stopped with error")
runtime.state.error = ""
runtime.start_server()
runtime.state.hosts = ["192.168.1.35", "10.0.0.4"]
draw_all("running")
bpy.ops.blender_drop.toggle_qr()
runtime.make_qr()
print("QR icon id:", runtime.qr_icon_id(), "(0 is normal in background mode: no GPU icons)")
draw_all("running + QR (no icon)")
real_icon = runtime.qr_icon_id
runtime.qr_icon_id = lambda: 123      # exercise the template_icon branch drawn in the GUI
draw_all("running + QR icon")
runtime.qr_icon_id = real_icon
for state in (jobs.UPLOADING, jobs.QUEUED, jobs.IMPORTING, jobs.IMPORTED, jobs.ERROR):
    job = runtime.state.store.create("Silla jardín %s.glb" % state.lower())
    runtime.state.store.update(job.id, state=state, message="Blender could not import the file: "
                               "a very long error message that must wrap nicely in the sidebar",
                               details="3 objects in 'Silla'")
    runtime.state.last_job_id = job.id
    draw_all("last job " + state)
bpy.ops.blender_drop.clear_history()
bpy.ops.blender_drop.refresh_addresses()
draw_all("after clear")
runtime.stop_server()
draw_all("stopped again")

print("layout calls checked:", len(calls), "template_icon calls:", calls.count("template_icon"))
for e in sorted(set(errors)):
    print("ERROR", e)
print("RESULT", "OK" if not errors else "FAILED")
sys.exit(1 if errors else 0)
