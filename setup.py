import os

import py2app.build_app
from setuptools import setup

# ---------------------------------------------------------------------------
# Compatibility patches for py2app 0.28 with modern setuptools / uv Python
# ---------------------------------------------------------------------------

# 1) py2app rejects install_requires, but setuptools auto-populates it from
#    pyproject.toml [project] dependencies. Clear it before py2app checks.
_orig_finalize = py2app.build_app.py2app.finalize_options


def _patched_finalize(self):
    self.distribution.install_requires = []
    _orig_finalize(self)


py2app.build_app.py2app.finalize_options = _patched_finalize

# 2) py2app resolves package names via imp.find_module, which fails on
#    namespace packages (like 'google') that lack __init__.py. Fall back
#    to importlib.
_orig_get_bootstrap = py2app.build_app.py2app.get_bootstrap


def _patched_get_bootstrap(self, bootstrap):
    import importlib.util

    try:
        return _orig_get_bootstrap(self, bootstrap)
    except ImportError:
        spec = importlib.util.find_spec(bootstrap)
        if spec and spec.submodule_search_locations:
            return list(spec.submodule_search_locations)[0]
        raise


py2app.build_app.py2app.get_bootstrap = _patched_get_bootstrap

# 3) py2app tries to copy zlib.__file__, but on statically-linked Python
#    (e.g. uv-managed) zlib is built-in with no __file__. Skip that copy.
import zlib as _zlib

if not getattr(_zlib, "__file__", None):
    _orig_build_executable = py2app.build_app.py2app.build_executable

    def _patched_build_executable(self, *args, **kwargs):
        import zlib

        sentinel = os.path.join(self.bdist_base, "_zlib_stub.so")
        os.makedirs(os.path.dirname(sentinel), exist_ok=True)
        open(sentinel, "w").close()
        zlib.__file__ = sentinel
        try:
            return _orig_build_executable(self, *args, **kwargs)
        finally:
            del zlib.__file__

    py2app.build_app.py2app.build_executable = _patched_build_executable

# 4) After building, remove any google/__init__.pyc from the zip. py2app's
#    modulegraph synthesizes one for namespace packages, which shadows the
#    extracted directory and breaks imports.
_orig_run = py2app.build_app.py2app.run


def _patched_run(self):
    _orig_run(self)
    # Strip bogus google/__init__.pyc from the site-packages zip
    from glob import glob

    for zpath in glob(os.path.join(self.dist_dir, "**", "*.zip"), recursive=True):
        _strip_namespace_init(zpath, "google")


def _strip_namespace_init(zip_path, namespace):
    """Remove a synthesized __init__.pyc for a namespace package from a zip."""
    import zipfile

    bad = f"{namespace}/__init__.pyc"
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if bad not in zf.namelist():
                return
            entries = [(n, zf.read(n)) for n in zf.namelist() if n != bad]
    except zipfile.BadZipFile:
        return
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)


py2app.build_app.py2app.run = _patched_run

# ---------------------------------------------------------------------------

APP = ["main.py"]
DATA_FILES = [
    (
        "media",
        [
            "media/menu_icon.png",
            "media/menu_icon_red.png",
            "media/menu_icon_yellow.png",
        ],
    )
]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "media/icon.icns",
    "plist": {
        "CFBundleName": "Open Transcribe",
        "CFBundleIdentifier": "com.open-transcribe",
        "LSUIElement": True,
        "NSRequiresAquaSystemAppearance": False,
        "NSMicrophoneUsageDescription": "Open Transcribe needs microphone access to record meetings.",
    },
    "packages": [
        "src",
        "rumps",
        "google",
        "notion_client",
        "sounddevice",
        "soundfile",
        "_sounddevice_data",
        "_soundfile_data",
        "charset_normalizer",
    ],
    "frameworks": [
        ".venv/lib/python3.12/site-packages/_sounddevice_data/portaudio-binaries/libportaudio.dylib",
        ".venv/lib/python3.12/site-packages/_soundfile_data/libsndfile_arm64.dylib",
    ],
}

setup(
    app=APP,
    name="Open Transcribe",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
)
