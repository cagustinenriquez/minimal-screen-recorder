"""UI test: a recording with no picture must be noticed, and the sound kept.

Covers 2.1.3, where the screen capture froze and nobody noticed for 18
minutes; the result was saved as a "recording" with sound and no picture.
Checks that:
  - the recording window warns when no new frames arrive
  - the warning goes away when frames come again
  - a recording with no frames saves the sound as .wav and says so
"""

import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.environ.get("APP_DIR", os.getcwd()))
import ui  # noqa: E402
from ui import ProgressWindow, RecorderApp  # noqa: E402

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))


class Stub:
    """Just the parts of RecorderApp the tested methods use."""

    CAPTURE_STALL_SECONDS = 0.5

    def __init__(self, root):
        self.root = root
        self.logs = []
        self.recorder = SimpleNamespace(
            recording=True, paused=False, frames_recorded=0, capture_error=None
        )
        timer = SimpleNamespace(is_running=False)
        self.progress_window = ProgressWindow(root, timer)
        self._watch_frames = -1
        self._watch_since = None
        self._capture_warned = False
        self.audio_file = None

    def _log(self, msg):
        self.logs.append(msg)

    def _watch_capture(self):
        RecorderApp._watch_capture(self)


def pump(root, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


def main():
    root = tk.Tk()
    root.withdraw()
    app = Stub(root)
    # Check often in the test instead of every 2 s
    root.after = (lambda orig: lambda ms, fn=None, *a: orig(min(ms, 100), fn, *a))(root.after)

    app._watch_capture()
    pump(root, 1.5)
    status = app.progress_window.status_label.cget("text")
    record("warns when no frames arrive", "NO PICTURE" in status, f"status: {status!r}")

    app.recorder.frames_recorded = 50
    pump(root, 0.5)
    status = app.progress_window.status_label.cget("text")
    record("warning clears when frames come back", "RECORDING" in status,
           f"status: {status!r}")

    # Paused: no frames is expected, no warning
    app.recorder.paused = True
    pump(root, 1.5)
    record("no warning while paused", not app._capture_warned)
    app.recorder.recording = False
    pump(root, 0.3)

    # Recording with no frames at all -> sound kept as .wav
    tmp = tempfile.mkdtemp()
    video = Path(tmp, "recording_temp.avi")
    video.write_bytes(b"x" * 7734)
    audio = Path(tmp, "temp_audio_1.wav")
    audio.write_bytes(b"RIFF....WAVE")
    app.audio_file = str(audio)
    shown = []
    ui.messagebox.showerror = lambda title, msg: shown.append((title, msg))
    out = RecorderApp._save_sound_only(app, str(video), str(Path(tmp, "recording_8.mp4")))
    pump(root, 0.3)
    record("no-picture recording keeps the sound as .wav",
           out == str(Path(tmp, "recording_8.wav")) and Path(out).exists()
           and not video.exists() and not audio.exists(),
           f"saved {out}")
    record("no-picture recording tells the user",
           bool(shown) and "no picture" in shown[0][1], f"{shown[:1]}")
    root.destroy()


try:
    main()
except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    record("ui test", False, str(e).strip().splitlines()[-1])

for name, ok, detail in results:
    line = f"{'PASS' if ok else 'FAIL'} {name}"
    if detail:
        line += f" ({detail})" if ok else f": {detail}"
    print(line, flush=True)
sys.exit(0 if results and all(ok for _, ok, _ in results) else 1)
