"""A double-clickable launcher app, and starting it at login.

macOS gives the microphone and keyboard permissions to whatever starts the script. Started at
login there is no Terminal or VS Code, so a small app bundle is made to hold those permissions.
"""

import plistlib
from pathlib import Path

from .config import ROOT

NAME = "Flow Backup"
_ID = "local.flowbackup"
APP = Path.home() / "Applications" / f"{NAME}.app"
LOG = Path.home() / "Library/Logs/FlowBackup.log"
_AGENT = Path.home() / "Library/LaunchAgents" / f"{_ID}.plist"

# The script stays running as the app, so the permissions belong to the bundle and not to Python.
_LAUNCHER = """#!/bin/bash
# Starts the dictation app from its project folder. Written by flow/login.py.
cd "{root}" || exit 1
"{root}/.venv/bin/python" -m flow.app > "{log}" 2>&1
"""


def install_app() -> Path:
    """Writes the launcher app. Safe to repeat; needed again if the project folder moves."""
    macos = APP / "Contents/MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    info = {
        "CFBundleName": NAME,
        "CFBundleIdentifier": _ID,
        "CFBundleExecutable": "launcher",
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        # Menu bar only: no Dock icon.
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": "Records your voice while you hold the dictation key.",
    }
    (APP / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    launcher = macos / "launcher"
    launcher.write_text(_LAUNCHER.format(root=ROOT, log=LOG))
    launcher.chmod(0o755)
    return APP


def starts_at_login() -> bool:
    return _AGENT.exists()


def set_start_at_login(on: bool) -> None:
    """Takes effect at the next login."""
    if not on:
        _AGENT.unlink(missing_ok=True)
        return
    install_app()
    _AGENT.parent.mkdir(parents=True, exist_ok=True)
    agent = {"Label": _ID, "ProgramArguments": ["/usr/bin/open", str(APP)], "RunAtLoad": True}
    _AGENT.write_bytes(plistlib.dumps(agent))
