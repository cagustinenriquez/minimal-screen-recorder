"""Microphone test: "Record my microphone" mixes the mic into the recording.

Uses a fake microphone (a virtual source fed by a null sink) and plays a
440 Hz beep through an app and a 1000 Hz beep into the mic at the same
moment. Checks that:
  - the default input is used as the microphone; a speaker monitor is not
  - both beeps end up in the recording, lined up in time
  - the mic isn't played back on the speakers (no echo of your own voice)
  - the separate mic file is removed after mixing
  - cancelling throws the mic recording away
"""

import os
import subprocess
import sys
import tempfile
import time
import wave

import numpy as np

sys.path.insert(0, os.environ.get("APP_DIR", os.getcwd()))
from audio import SystemAudioCapture  # noqa: E402

results = []
RATE = 48000


def record(name, ok, detail=""):
    results.append((name, ok, detail))


def pactl(*args):
    return subprocess.run(
        ["pactl", *args], capture_output=True, text=True, check=True
    ).stdout


def make_beep(path, freq, at=1.0, length=0.3, total=3.0):
    """Silence with one beep starting at `at` seconds."""
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=f={freq}:duration={length}", "-af",
         f"adelay={int(at * 1000)}:all=1,apad=whole_dur={total},volume=-6dB",
         "-ar", str(RATE), "-ac", "2", path],
        check=True,
    )


def onset(path, freq):
    """Time (s) the given frequency first gets loud, in 10 ms steps."""
    with wave.open(path) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        rate, channels = w.getframerate(), w.getnchannels()
    mono = data.reshape(-1, channels).mean(axis=1)
    step = rate // 100
    window = step * 2
    t = np.arange(window) / rate
    probe = np.exp(-2j * np.pi * freq * t)
    levels = [abs(np.dot(mono[i:i + window], probe)) / window
              for i in range(0, len(mono) - window, step)]
    levels = np.array(levels)
    if not len(levels) or levels.max() < 50:
        return None
    first = int(np.argmax(levels > levels.max() * 0.3))
    return first * step / rate


def main():
    tmp = tempfile.mkdtemp()
    app_beep = os.path.join(tmp, "app.wav")
    mic_beep = os.path.join(tmp, "mic.wav")
    make_beep(app_beep, 440)
    make_beep(mic_beep, 1000)

    sinks = [line.split("\t")[1] for line in pactl("list", "short", "sinks").splitlines()]
    if "test_speakers" not in sinks:  # an earlier test may have made it
        pactl("load-module", "module-null-sink", "sink_name=test_speakers")
    pactl("set-default-sink", "test_speakers")
    # Fake microphone: whatever plays into test_mic_feed comes out of test_mic
    pactl("load-module", "module-null-sink", "sink_name=test_mic_feed")
    pactl("load-module", "module-virtual-source", "source_name=test_mic",
          "master=test_mic_feed.monitor")

    # No microphone: default input is a speaker monitor -> not used
    pactl("set-default-source", "test_speakers.monitor")
    probe = SystemAudioCapture(["apptone"])
    record("a speaker monitor isn't taken for a microphone",
           probe.get_microphone() is None)

    pactl("set-default-source", "test_mic")
    out = os.path.join(tmp, "recorded.wav")
    capture = SystemAudioCapture(["apptone"], record_microphone=True)
    # The app has to be playing when setup moves it, so keep a quiet player
    # going and let the beeps come from the same client name.
    keep = subprocess.Popen(["paplay", "--client-name=apptone", "--volume=0",
                             app_beep])
    time.sleep(0.5)
    if not capture.setup():
        record("audio setup with microphone", False, "setup failed")
        return
    record("default input used as the microphone", capture.mic_source == "test_mic",
           f"mic: {capture.mic_source}")
    capture.start_recording(out)
    time.sleep(0.5)
    # Beep into the app and into the mic at the same moment
    players = [
        subprocess.Popen(["paplay", "--client-name=apptone",
                          "--device=record_sink", app_beep]),
        subprocess.Popen(["paplay", "--client-name=micfeed",
                          "--device=test_mic_feed", mic_beep]),
    ]
    for p in players:
        p.wait(timeout=10)
    time.sleep(0.5)

    loops_from_mic = [line for line in pactl("list", "short", "modules").splitlines()
                      if "module-loopback" in line and "test_mic" in line]
    record("microphone isn't played on the speakers", not loops_from_mic,
           f"loopbacks from the mic: {loops_from_mic}")

    mic_file = capture.mic_file
    capture.stop_recording()
    keep.kill()
    capture.cleanup()

    app_at, mic_at = onset(out, 440), onset(out, 1000)
    record("recording has the app sound", app_at is not None)
    record("recording has the microphone", mic_at is not None)
    if app_at is not None and mic_at is not None:
        diff = (mic_at - app_at) * 1000
        record("microphone lined up with the app sound", abs(diff) < 80,
               f"app beep {app_at:.2f}s, mic beep {mic_at:.2f}s, off by {diff:+.0f} ms")
    record("separate mic file removed after mixing",
           bool(mic_file) and not os.path.exists(mic_file), f"{mic_file}")

    # Cancel: the mic recording is thrown away, not mixed
    out2 = os.path.join(tmp, "cancelled.wav")
    capture = SystemAudioCapture([], record_microphone=True)
    capture.setup()
    capture.start_recording(out2)
    time.sleep(1)
    mic_file = capture.mic_file
    capture.stop_recording(mix_microphone=False)
    capture.cleanup()
    record("cancel throws the mic recording away",
           bool(mic_file) and not os.path.exists(mic_file), f"{mic_file}")


try:
    main()
except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    record("microphone test", False, str(e).strip().splitlines()[-1])

for name, ok, detail in results:
    line = f"{'PASS' if ok else 'FAIL'} {name}"
    if detail:
        line += f" ({detail})" if ok else f": {detail}"
    print(line, flush=True)
sys.exit(0 if results and all(ok for _, ok, _ in results) else 1)
