"""Finds newer Bolo releases and installs them.

A release is a git tag like v0.2.0 on GitHub. Checking asks git for the tag list, so it needs no
GitHub account and no API quota. Updating runs install.sh, which fetches the newest release,
rebuilds Bolo.app and opens it again.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from .config import ROOT, USER_DIR

log = logging.getLogger("bolo")

REPO = "https://github.com/soumyaux/bolo.git"
RELEASES = "https://github.com/soumyaux/bolo/releases"
LOG = Path.home() / "Library/Logs/Bolo.log"
_TAG = re.compile(r"refs/tags/v(\d+)\.(\d+)\.(\d+)$")


def current() -> str:
    """This copy's version, from pyproject.toml."""
    try:
        return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "0.0.0"


def parse(version: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version.strip())
    return tuple(int(n) for n in match.groups()) if match else None


def newest(ls_remote: str) -> str | None:
    """The highest vX.Y.Z tag in `git ls-remote --tags` output, as "X.Y.Z"."""
    versions = [tuple(int(n) for n in m.groups()) for line in ls_remote.splitlines() if (m := _TAG.search(line))]
    return ".".join(map(str, max(versions))) if versions else None


def is_newer(latest: str | None, installed: str) -> bool:
    new, old = parse(latest or ""), parse(installed)
    return bool(new and old and new > old)


def latest() -> str | None:
    """The newest release on GitHub. Raises OSError or subprocess errors when offline."""
    out = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", REPO],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    ).stdout
    return newest(out)


def can_install() -> bool:
    """True for a copy set up by install.sh; a developer's checkout is left to git."""
    return ROOT == USER_DIR / "app" and (ROOT / "install.sh").exists()


def install() -> None:
    """Runs the installer in the background. It quits this Bolo and opens the new one."""
    # A copy, since updating rewrites install.sh while bash is still reading it.
    script = Path(tempfile.mkdtemp()) / "install.sh"
    shutil.copy(ROOT / "install.sh", script)
    env = {**os.environ, "PATH": f"{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    with open(LOG, "a") as out:
        subprocess.Popen(
            ["/bin/bash", str(script)],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            env=env,
            start_new_session=True,
        )
    log.info("update started")
