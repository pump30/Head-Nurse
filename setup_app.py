"""Build HeadNurse.app: python setup_app.py py2app"""
# Prevent setuptools from loading pyproject.toml dependencies as install_requires,
# which py2app>=0.28 explicitly rejects with "install_requires is no longer supported".
import setuptools.dist as _dist_mod

_orig_parse_config = _dist_mod.Distribution.parse_config_files


def _skip_pyproject_config(self, filenames=None, **kwargs):
    if filenames is None:
        filenames = self.find_config_files()
    filenames = [f for f in filenames if "pyproject.toml" not in f]
    return _orig_parse_config(self, filenames, **kwargs)


_dist_mod.Distribution.parse_config_files = _skip_pyproject_config

from setuptools import setup  # noqa: E402  (must come after patch)

APP = ["src/kanban_agent/menubar.py"]
DATA_FILES = [("", ["resources/icon-on.png", "resources/icon-off.png"])]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "HeadNurse",
        "CFBundleDisplayName": "HeadNurse",
        "CFBundleIdentifier": "com.kanban-agent.menubar",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
    "packages": ["kanban_agent", "rumps", "yaml"],
    "includes": ["kanban_agent.agent", "kanban_agent.menubar", "kanban_agent.status"],
}

setup(
    app=APP,
    name="HeadNurse",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
)
