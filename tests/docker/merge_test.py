"""Merge test: the step that adds the sound to the video must not lose it.

Covers what went wrong in 2.1.2 on a 9-minute recording: the progress window
was closed mid-merge, the progress update raised an error and the merge was
stopped, leaving a video with no sound. Checks that:
  - an error in the progress update doesn't stop the merge
  - if the first merge fails, the fast retry (copying the video) still
    produces a file with sound
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.environ.get("APP_DIR", os.getcwd()))
from video import merge_audio_video  # noqa: E402

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))


def streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", path], capture_output=True, text=True,
    ).stdout.split()
    return out


def main():
    tmp = tempfile.mkdtemp()
    video = os.path.join(tmp, "video_temp.avi")
    audio = os.path.join(tmp, "audio.wav")
    # Same kind of input the app records: MPEG-4 Part 2 (XviD) in AVI + WAV
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=640x360:rate=20:duration=20",
                    "-c:v", "mpeg4", "-vtag", "XVID", video], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "sine=f=440:duration=20", "-ac", "2", audio], check=True)

    # 1. Progress window gone mid-merge
    calls = []

    def broken_progress(percentage, info):
        calls.append(percentage)
        if len(calls) > 1:
            raise RuntimeError('invalid command name ".!toplevel4.!label2"')

    out1 = os.path.join(tmp, "out1.mp4")
    ok = merge_audio_video(video, audio, out1, video_codec="libx264",
                           progress_callback=broken_progress)
    record("merge survives a failing progress update",
           ok and "audio" in streams(out1),
           f"streams: {streams(out1) if os.path.exists(out1) else 'no file'}")

    # 2. First attempt fails (codec that doesn't exist) -> fast retry
    out2 = os.path.join(tmp, "out2.mp4")
    ok = merge_audio_video(video, audio, out2, video_codec="no_such_codec",
                           progress_callback=lambda p, i: None)
    record("retry without re-encoding gives sound",
           ok and set(streams(out2)) == {"video", "audio"},
           f"streams: {streams(out2) if os.path.exists(out2) else 'no file'}")


try:
    main()
except Exception as e:  # noqa: BLE001
    record("merge test", False, str(e).strip().splitlines()[-1])

for name, ok, detail in results:
    line = f"{'PASS' if ok else 'FAIL'} {name}"
    if detail:
        line += f" ({detail})" if ok else f": {detail}"
    print(line, flush=True)
sys.exit(0 if results and all(ok for _, ok, _ in results) else 1)
