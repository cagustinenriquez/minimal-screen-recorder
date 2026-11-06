"""Audio routing test: records a tone through the app's virtual sink.

Needs a running PulseAudio (or pipewire-pulse) server, ffmpeg and paplay.
Simulates a crashed earlier run by loading a leftover record_sink and
loopback first, then checks that:
  - setup removes the leftovers (only one loopback, so no doubled audio)
  - the playing app is moved to record_sink
  - the recording has sound
  - cleanup leaves no record_sink modules behind
"""

import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.environ.get("APP_DIR", os.getcwd()))
from audio import SystemAudioCapture  # noqa: E402

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))


def pactl(*args):
    return subprocess.run(
        ["pactl", *args], capture_output=True, text=True, check=True
    ).stdout


def record_sink_modules():
    mods = [line.split("\t") for line in pactl("list", "short", "modules").splitlines()]
    sinks = [m for m in mods if m[1] == "module-null-sink" and "sink_name=record_sink" in m[2]]
    loops = [m for m in mods if m[1] == "module-loopback" and "record_sink.monitor" in m[2]]
    return sinks, loops


def mean_volume(path):
    out = subprocess.run(
        ["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    ).stderr
    m = re.search(r"mean_volume: (-?[\d.]+) dB", out)
    return float(m.group(1)) if m else float("-inf")


def main():
    tmp = tempfile.mkdtemp()
    tone = os.path.join(tmp, "tone.wav")
    out = os.path.join(tmp, "recorded.wav")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "sine=f=440:duration=30", "-af", "volume=-12dB", tone],
        check=True,
    )

    # Stand-in for real speakers. Without a sound card PulseAudio only has a
    # placeholder sink (auto_null) that it removes as soon as any other sink
    # appears, which would leave the loopback with nowhere to play.
    pactl("load-module", "module-null-sink", "sink_name=test_speakers")
    pactl("set-default-sink", "test_speakers")

    # Leftovers from a run that crashed or was killed.
    default_sink = pactl("get-default-sink").strip()
    pactl("load-module", "module-null-sink", "sink_name=record_sink")
    pactl("load-module", "module-loopback", "source=record_sink.monitor",
          f"sink={default_sink}")

    player = subprocess.Popen(["paplay", tone])  # application.name = "paplay"
    time.sleep(1.0)
    capture = SystemAudioCapture(["paplay"])
    try:
        ok = capture.setup()
        record("audio setup", ok)
        if not ok:
            return

        sinks, loops = record_sink_modules()
        record("leftover routing removed", len(sinks) == 1 and len(loops) == 1,
               f"{len(sinks)} record_sink, {len(loops)} loopback (want 1 and 1)")

        record_sink_id = next(
            line.split("\t")[0]
            for line in pactl("list", "short", "sinks").splitlines()
            if line.split("\t")[1] == "record_sink"
        )
        moved = any(
            line.split("\t")[1] == record_sink_id
            for line in pactl("list", "short", "sink-inputs").splitlines()
        )
        record("playing app moved to record_sink", moved)

        capture.start_recording(out)
        time.sleep(3.0)
        capture.stop_recording()
        level = mean_volume(out)
        record("recording has sound", level > -50, f"mean {level:.1f} dB")
    finally:
        capture.cleanup()
        player.kill()

    sinks, loops = record_sink_modules()
    record("nothing left after cleanup", not sinks and not loops,
           f"{len(sinks)} record_sink, {len(loops)} loopback")


try:
    main()
except Exception as e:  # noqa: BLE001
    record("audio routing test", False, str(e).strip().splitlines()[-1])

for name, ok, detail in results:
    line = f"{'PASS' if ok else 'FAIL'} {name}"
    if detail:
        line += f" ({detail})" if ok else f": {detail}"
    print(line, flush=True)
sys.exit(0 if results and all(ok for _, ok, _ in results) else 1)
