"""Picks the writing style for the app the user is dictating into, from [styles] in config.toml."""

import logging
from typing import NamedTuple
from urllib.parse import urlparse

from AppKit import NSWorkspace

from .learn import ax

log = logging.getLogger(__name__)

STYLES = ("default", "prompt", "casual", "formal")
BROWSERS = ax.CHROMIUM | {"com.apple.Safari", "org.mozilla.firefox"}


class Target(NamedTuple):
    """Where the text will be pasted."""

    app: str
    bundle_id: str
    pid: int
    style: str


def _checked(style: str, where: str) -> str:
    if style in STYLES:
        return style
    log.warning("style %r for %s is not one of %s; using default", style, where, ", ".join(STYLES))
    return "default"


def for_app(cfg: dict, name: str, bundle_id: str) -> str | None:
    """The style set for this app by bundle ID, then by name; None when it is not listed."""
    apps = cfg.get("styles", {}).get("apps", {})
    if bundle_id and bundle_id in apps:
        return _checked(apps[bundle_id], bundle_id)
    lowered = {key.lower(): value for key, value in apps.items()}
    if name and name.lower() in lowered:
        return _checked(lowered[name.lower()], name)
    return None


def for_page(cfg: dict, url: str, title: str) -> str | None:
    """The style for a web page by its address, or by words in the window title when the address
    cannot be read. A site entry also covers its subdomains."""
    styles = cfg.get("styles", {})
    host = (urlparse(url).hostname or "").lower() if url else ""
    if host:
        for site, style in styles.get("sites", {}).items():
            site = site.lower()
            if host == site or host.endswith("." + site):
                return _checked(style, site)
        return None
    for word, style in styles.get("titles", {}).items():
        if word.lower() in title.lower():
            return _checked(style, word)
    return None


def detect(cfg: dict) -> Target:
    """The front app and its style. Pages are read only in browsers, where the app says nothing."""
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return Target("", "", 0, "default")
    name, bundle_id, pid = app.localizedName() or "", app.bundleIdentifier() or "", app.processIdentifier()
    style = for_app(cfg, name, bundle_id)
    if style is None and bundle_id in BROWSERS:
        style = for_page(cfg, *ax.page(pid))
    return Target(name, bundle_id, pid, style or "default")
