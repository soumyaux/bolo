"""Checks which macOS privacy permissions Bolo has."""

from pathlib import Path

import objc
import Quartz
from AppKit import NSBundle, NSRunningApplication
from ApplicationServices import AXIsProcessTrusted


def _sees_window_titles() -> bool:
    """macOS hides other apps' window titles without Screen Recording. The preflight call can
    answer no for an unsigned app that has it, so this checks what the app can actually see."""
    windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    me = NSRunningApplication.currentApplication().processIdentifier()
    # The Dock's and the menu bar's window titles are visible to every app.
    return any(
        w.get("kCGWindowName")
        for w in windows or []
        if w.get("kCGWindowOwnerPID") != me and w.get("kCGWindowOwnerName") not in ("Dock", "Window Server")
    )


def permissions() -> dict[str, bool]:
    """Which macOS privacy permissions this app has. Asks for none of them."""
    NSBundle.bundleWithPath_("/System/Library/Frameworks/AVFoundation.framework").load()
    microphone = objc.lookUpClass("AVCaptureDevice").authorizationStatusForMediaType_("soun") == 3
    # Only an app with Full Disk Access can open the privacy database.
    try:
        open(Path.home() / "Library/Application Support/com.apple.TCC/TCC.db", "rb").close()
        full_disk = True
    except OSError:
        full_disk = False
    return {
        "Accessibility": bool(AXIsProcessTrusted()),
        "Input Monitoring": bool(Quartz.CGPreflightListenEventAccess()),
        "Microphone": microphone,
        "Screen Recording": bool(Quartz.CGPreflightScreenCaptureAccess()) or _sees_window_titles(),
        "Full Disk Access": full_disk,
    }
