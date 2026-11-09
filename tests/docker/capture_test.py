"""Wayland capture test: recording must not freeze before the first picture.

Covers what went wrong in 2.1.3: when the desktop hadn't sent a picture yet,
grab() locked itself up, the recording thread froze, and an 18-minute
recording came out with sound but no video. Checks that:
  - grab() returns a black frame (doesn't hang) before the first picture
  - grab() returns the picture once one arrives
  - each screen-share request gets a new name, also across recordings
"""

import os
import sys
import threading

import numpy as np

sys.path.insert(0, os.environ.get("APP_DIR", os.getcwd()))
import wayland_capture  # noqa: E402
from wayland_capture import PortalFrameSource  # noqa: E402

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))


def fake_source():
    """A PortalFrameSource without D-Bus/GStreamer: stream open, no picture yet."""
    src = PortalFrameSource.__new__(PortalFrameSource)
    src._latest = None
    src._frame_lock = threading.Lock()
    src._frame_ready = threading.Event()
    src._stream_size = (320, 200)
    return src


def grab_with_timeout(src, timeout=2.0):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("frame", src.grab()), daemon=True)
    t.start()
    t.join(timeout)
    return out.get("frame")


def main():
    src = fake_source()
    frame = grab_with_timeout(src)
    record("grab before the first picture doesn't hang",
           frame is not None and frame.shape == (200, 320, 3) and not frame.any(),
           "hung" if frame is None else f"shape {frame.shape}")

    picture = np.full((200, 320, 3), 255, dtype=np.uint8)
    if not src._frame_lock.acquire(timeout=1):
        record("grab returns the picture once it arrives", False,
               "the frame lock is stuck")
        return
    src._latest = picture
    src._frame_lock.release()
    frame = grab_with_timeout(src)
    record("grab returns the picture once it arrives",
           frame is not None and frame.all(), "hung" if frame is None else "")

    a, b = fake_source(), fake_source()
    names = [a._new_token(), a._new_token(), b._new_token(), b._new_token()]
    record("screen-share request names never repeat",
           len(set(names)) == len(names), ", ".join(names))


try:
    main()
except Exception as e:  # noqa: BLE001
    record("capture test", False, str(e).strip().splitlines()[-1])

for name, ok, detail in results:
    line = f"{'PASS' if ok else 'FAIL'} {name}"
    if detail:
        line += f" ({detail})" if ok else f": {detail}"
    print(line, flush=True)
# A frozen grab() thread would keep the process alive; exit hard.
sys.stdout.flush()
os._exit(0 if results and all(ok for _, ok, _ in results) else 1)
