"""Smoke checks run inside the screenrec-lite snap sandbox.

Run with the snap's own Python, e.g.:
    snap run --shell screenrec-lite -c '$SNAP/bin/python3 checks.py'

Prints one "PASS name" / "FAIL name: reason" line per check and exits
non-zero if any check failed. Needs an X display (DISPLAY) for the GUI
checks; the test runner starts Xvfb for that.
"""

import os
import subprocess
import sys
import traceback

APP_DIR = os.path.join(os.environ.get("SNAP", ""), "app")
sys.path.insert(0, APP_DIR)
import snap_compat  # noqa: E402,F401  # same library fix the app applies

results = []


def check(name):
    def wrap(fn):
        try:
            detail = fn()
            results.append((name, True, detail or ""))
        except Exception as e:  # noqa: BLE001 - report every failure
            msg = str(e).strip().splitlines()[-1] if str(e).strip() else repr(e)
            results.append((name, False, msg))
            if os.environ.get("CHECKS_VERBOSE"):
                traceback.print_exc()
        return fn

    return wrap


def run(cmd):
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip()[-300:])
    return out.stdout


@check("python libraries")
def _():
    import cv2
    import mss  # noqa: F401
    import numpy

    return f"cv2 {cv2.__version__}, numpy {numpy.__version__}"


@check("tkinter")
def _():
    import tkinter

    return f"Tk {tkinter.TkVersion}"


@check("app modules import")
def _():
    import audio  # noqa: F401
    import config  # noqa: F401
    import ui  # noqa: F401
    import utils  # noqa: F401
    import video  # noqa: F401
    import wayland_capture  # noqa: F401


@check("ffmpeg")
def _():
    return run(["ffmpeg", "-version"]).splitlines()[0]


@check("ffprobe")
def _():
    return run(["ffprobe", "-version"]).splitlines()[0]


@check("pactl")
def _():
    return run(["pactl", "--version"]).splitlines()[0]


@check("ffmpeg can encode")
def _():
    out = os.path.join(os.environ.get("SNAP_USER_COMMON", "/tmp"), "check.mp4")
    run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=1",
            "-f", "lavfi", "-i", "sine=f=440:duration=1",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", out,
        ]
    )
    size = os.path.getsize(out)
    os.remove(out)
    return f"{size} bytes"


@check("gstreamer + pipewire plugin")
def _():
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstApp", "1.0")
    from gi.repository import Gst, GstApp  # noqa: F401

    Gst.init(None)
    missing = [
        e
        for e in ("pipewiresrc", "videoconvert", "appsink")
        if Gst.ElementFactory.find(e) is None
    ]
    if missing:
        raise RuntimeError(f"missing elements: {', '.join(missing)}")
    return Gst.version_string()


@check("opencv video writer")
def _():
    import cv2
    import numpy as np

    out = os.path.join(os.environ.get("SNAP_USER_COMMON", "/tmp"), "check.avi")
    w = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*"XVID"), 10, (320, 240))
    if not w.isOpened():
        raise RuntimeError("XVID writer did not open")
    for _ in range(10):
        w.write(np.zeros((240, 320, 3), dtype=np.uint8))
    w.release()
    size = os.path.getsize(out)
    os.remove(out)
    return f"{size} bytes"


@check("window opens (X11)")
def _():
    import tkinter

    root = tkinter.Tk(className="screenrec-lite")
    root.update()
    root.destroy()


@check("screen capture (X11)")
def _():
    import mss

    with mss.mss() as sct:
        mon = sct.monitors[1]
        shot = sct.grab(mon)
    return f"{shot.size[0]}x{shot.size[1]}"


for name, ok, detail in results:
    line = f"{'PASS' if ok else 'FAIL'} {name}"
    if detail:
        line += f": {detail}" if not ok else f" ({detail})"
    print(line, flush=True)

sys.exit(0 if all(ok for _, ok, _ in results) else 1)
