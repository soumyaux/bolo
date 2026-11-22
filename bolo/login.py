"""A double-clickable launcher app, and starting it at login.

macOS gives the microphone and keyboard permissions to the app the user started. Started at
login there is no Terminal or VS Code, so a small app bundle is made to hold those permissions.

Run: uv run python -m bolo.login              (builds or updates ~/Applications/Bolo.app)
     uv run python -m bolo.login --at-login   (also starts it at login)
"""

import hashlib
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import ROOT

NAME = "Bolo"
_ID = "local.bolo"
APP = Path.home() / "Applications" / f"{NAME}.app"
LOG = Path.home() / "Library/Logs/Bolo.log"
_AGENT = Path.home() / "Library/LaunchAgents" / f"{_ID}.plist"
_SOURCE = Path(__file__).with_name("launcher.c")
# The app's own icon, so Finder, the Dock and the quit animation show it even when Bolo is not running.
_ICON = ROOT / "assets" / "Bolo.icns"
# A code signing certificate in the login keychain. Signed with it, the app keeps its permissions
# when it is rebuilt; signed without one, every rebuild needs them granted again.
_CERTIFICATE = "Bolo Local Signing"
# Info.plist key holding what the installed launcher was built from.
_BUILT_FROM = "BoloLauncherBuild"


def _build_id() -> str:
    return hashlib.sha256(f"{_SOURCE.read_bytes()!r}{_ICON.read_bytes()!r}{ROOT}{LOG}{_signing_identity()}".encode()).hexdigest()[:16]


def _signing_identity() -> str:
    """The certificate's name if it is in the keychain, else "-" for an ad hoc signature."""
    found = subprocess.run(["security", "find-identity", "-p", "codesigning"], capture_output=True, text=True)
    return _CERTIFICATE if f'"{_CERTIFICATE}"' in found.stdout else "-"


def ensure_certificate() -> bool:
    """Creates the self-signed signing certificate in the login keychain when it is missing.
    It stays untrusted, which codesign accepts, so no admin password is needed. True if present."""
    if _signing_identity() == _CERTIFICATE:
        return True
    with tempfile.TemporaryDirectory() as tmp:
        key, cert, p12 = (str(Path(tmp) / n) for n in ("key.pem", "cert.pem", "cert.p12"))
        password = hashlib.sha256(tmp.encode()).hexdigest()[:24]
        config = Path(tmp) / "openssl.cnf"
        config.write_text(
            "[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n"
            f"[dn]\nCN={_CERTIFICATE}\n"
            "[ext]\nbasicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature\n"
            "extendedKeyUsage=critical,codeSigning\n"
        )
        steps = [
            ["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
             "-keyout", key, "-out", cert, "-config", str(config)],
            ["/usr/bin/openssl", "pkcs12", "-export", "-inkey", key, "-in", cert, "-out", p12,
             "-name", _CERTIFICATE, "-passout", f"pass:{password}"],
            # -T lets codesign use the key without asking each time.
            ["security", "import", p12, "-P", password, "-T", "/usr/bin/codesign"],
        ]
        for step in steps:
            if subprocess.run(step, capture_output=True).returncode != 0:
                print(f"Could not create the signing certificate ({step[0]} failed); signing ad hoc.")
                return False
    return _signing_identity() == _CERTIFICATE


def install_app() -> Path:
    """Builds the launcher app. Leaves it untouched when already up to date, because macOS ties the
    granted permissions to the exact files and a rebuild can make it forget them."""
    build_id = _build_id()
    try:
        info = plistlib.loads((APP / "Contents/Info.plist").read_bytes())
        if info.get(_BUILT_FROM) == build_id:
            return APP
    except (OSError, plistlib.InvalidFileException):
        pass
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / APP.name
        (app / "Contents/MacOS").mkdir(parents=True)
        info = {
            "CFBundleName": NAME,
            "CFBundleIdentifier": _ID,
            "CFBundleExecutable": "launcher",
            "CFBundlePackageType": "APPL",
            "CFBundleIconFile": "Bolo",
            "CFBundleVersion": "1",
            # Menu bar only: no Dock icon.
            "LSUIElement": True,
            "NSMicrophoneUsageDescription": "Records your voice while you hold the dictation key.",
            _BUILT_FROM: build_id,
        }
        (app / "Contents/Resources").mkdir()
        shutil.copy(_ICON, app / "Contents/Resources/Bolo.icns")
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        subprocess.run(
            ["cc", "-O2", f'-DROOT="{ROOT}"', f'-DLOG="{LOG}"', "-o", str(app / "Contents/MacOS/launcher"), str(_SOURCE)],
            check=True,
        )
        subprocess.run(["codesign", "--force", "-s", _signing_identity(), "-i", _ID, str(app)], check=True)
        APP.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(APP, ignore_errors=True)
        shutil.move(str(app), APP)
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


if __name__ == "__main__":
    ensure_certificate()
    print(install_app())
    if "--at-login" in sys.argv[1:]:
        set_start_at_login(True)
        print("Starts at login")
