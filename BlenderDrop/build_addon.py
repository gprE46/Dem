#!/usr/bin/env python3
"""Build dist/BlenderDrop.zip, installable from Blender:

    Edit > Preferences > Add-ons > (v menu) Install from Disk...  (Blender 4.2+)
    Edit > Preferences > Add-ons > Install...                       (Blender 4.0/4.1)

Usage:  python build_addon.py            (no dependencies, any Python 3.8+)
"""

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGE = "blender_drop"
SOURCE = os.path.join(ROOT, "addon", PACKAGE)
DIST = os.path.join(ROOT, "dist")
OUTPUT = os.path.join(DIST, "BlenderDrop.zip")

REQUIRED = [
    "__init__.py", "blender_manifest.toml", "importer.py", "jobs.py", "multipart.py",
    "qr.py", "runtime.py", "server.py", "ui.py", "utils.py",
    "web/index.html", "web/style.css", "web/app.js",
]
EXCLUDED_DIRS = {"__pycache__", ".git", ".pytest_cache"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".DS_Store", ".orig", ".rej")
FIXED_DATE = (2026, 1, 1, 0, 0, 0)   # reproducible archive


def collect_files():
    files = []
    for folder, dirs, names in os.walk(SOURCE):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith("."))
        for name in sorted(names):
            if name.startswith(".") or name.endswith(EXCLUDED_SUFFIXES):
                continue
            full = os.path.join(folder, name)
            files.append((full, os.path.relpath(full, SOURCE).replace(os.sep, "/")))
    return files


def check_sources(files):
    names = {rel for _, rel in files}
    missing = [r for r in REQUIRED if r not in names]
    if missing:
        sys.exit("Missing files: " + ", ".join(missing))
    for full, rel in files:
        if rel.endswith(".py"):
            with open(full, "rb") as handle:
                compile(handle.read(), rel, "exec")   # raises SyntaxError if broken


def build():
    files = collect_files()
    check_sources(files)
    os.makedirs(DIST, exist_ok=True)
    tmp = OUTPUT + ".tmp"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for full, rel in files:
            info = zipfile.ZipInfo(PACKAGE + "/" + rel, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(full, "rb") as handle:
                archive.writestr(info, handle.read())
    os.replace(tmp, OUTPUT)
    with zipfile.ZipFile(OUTPUT) as archive:
        bad = archive.testzip()
        if bad:
            sys.exit("Corrupted entry in zip: " + bad)
        entries = archive.namelist()
    print("Built %s (%d files, %.1f KB)" % (OUTPUT, len(entries), os.path.getsize(OUTPUT) / 1024))
    for entry in entries:
        print("  " + entry)
    return OUTPUT


if __name__ == "__main__":
    build()
