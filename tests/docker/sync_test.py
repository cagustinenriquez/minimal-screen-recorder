"""Audio/video sync test: flashes the screen white and plays a beep at the
same moment, twice, then checks the finished recording.

Follows the app's own steps (audio setup, audio start, video start, stop
video, stop audio, merge) on an X11 display with a PulseAudio server.
Checks that:
  - the video lasts as long as the recording really took (no drift)
  - each beep lines up with its flash in the merged file
"""

import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk

import cv2
import numpy as np

sys.path.insert(0, os.environ.get("APP_DIR", os.getcwd()))
from audio import SystemAudioCapture  # noqa: E402
from video import ScreenRecorder, merge_audio_video  # noqa: E402

FPS = 60  # high on purpose: capture falls behind, which used to cause drift
TOLERANCE_S = 0.15  # a beep starting process + a couple of frames
results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))


def pactl(*args):
    return subprocess.run(["pactl", *args], capture_output=True, text=True,
                          check=True).stdout


def wait(root, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.005)


def flash_and_beep(root, beep):
    root.configure(bg="white")
    root.update()
    subprocess.Popen(["paplay", "--device=record_sink", "--latency-msec=20", beep])
    wait(root, 0.4)
    root.configure(bg="black")
    root.update()


def flash_times(path):
    cap = cv2.VideoCapture(path)
    times, was_white = [], False
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        white = frame.mean() > 128
        if white and not was_white:
            times.append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000)
        was_white = white
    return times


def beep_times(path, rate=48000):
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a", "-ac", "1",
         "-ar", str(rate), "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    samples = np.abs(np.frombuffer(pcm, dtype=np.int16).astype(np.float32))
    loud = np.flatnonzero(samples > 0.3 * samples.max())
    times, last = [], -10.0
    for i in loud:
        t = i / rate
        if t - last > 1.0:
            times.append(t)
        last = t
    return times


def main():
    tmp = tempfile.mkdtemp()
    beep = os.path.join(tmp, "beep.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "sine=f=1000:duration=0.2", beep], check=True)
    pactl("load-module", "module-null-sink", "sink_name=test_speakers")
    pactl("set-default-sink", "test_speakers")

    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
    root.configure(bg="black")
    wait(root, 0.5)

    audio = SystemAudioCapture(["none"])
    video = ScreenRecorder(FPS)
    try:
        if not audio.setup():
            record("sync test setup", False, "audio setup failed")
            return
        # Same order as the app (ui._begin_recording)
        audio.start_recording(os.path.join(tmp, "audio.wav"))
        time.sleep(0.1)
        video.start_recording(os.path.join(tmp, "video.avi"))

        wait(root, 3.0)
        flash_and_beep(root, beep)
        wait(root, 4.0)
        flash_and_beep(root, beep)
        wait(root, 2.0)

        stopped = time.time()
        video.stop_recording()
        elapsed = stopped - video.first_frame_time
        time.sleep(1.0)  # same as the app
        audio.stop_recording()
    finally:
        audio.cleanup()
        root.destroy()

    lead = video.first_frame_time - audio.start_time
    record("start times measured", audio.start_time is not None,
           f"audio started {lead * 1000:.0f} ms before video")

    raw_video = os.path.join(tmp, "video.avi")
    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", raw_video], capture_output=True, text=True,
    ).stdout)
    record("video length matches real time", abs(duration - elapsed) < 0.3,
           f"{duration:.2f}s of video for {elapsed:.2f}s recorded, "
           f"{video.frames_dropped} late frames repeated")

    out = os.path.join(tmp, "merged.mp4")
    ok = merge_audio_video(raw_video, os.path.join(tmp, "audio.wav"), out,
                           video_codec="libx264", audio_lead_s=lead)
    record("merge", ok)
    if not ok:
        return

    flashes, beeps = flash_times(out), beep_times(out)
    if len(flashes) != 2 or len(beeps) != 2:
        record("found 2 flashes and 2 beeps", False,
               f"flashes at {flashes}, beeps at {beeps}")
        return
    for n, (f, b) in enumerate(zip(flashes, beeps), 1):
        record(f"beep {n} in sync with flash", abs(b - f) < TOLERANCE_S,
               f"flash {f:.3f}s, beep {b:.3f}s, off by {(b - f) * 1000:+.0f} ms")


try:
    main()
except Exception as e:  # noqa: BLE001
    record("sync test", False, str(e).strip().splitlines()[-1])

for name, ok, detail in results:
    line = f"{'PASS' if ok else 'FAIL'} {name}"
    if detail:
        line += f" ({detail})" if ok else f": {detail}"
    print(line, flush=True)
sys.exit(0 if results and all(ok for _, ok, _ in results) else 1)
