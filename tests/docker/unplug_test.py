"""Unplug test: USB audio devices disappearing in the middle of a recording.

A USB interface (like a Focusrite Scarlett) is an audio device that can
vanish at any moment. Simulated here with virtual devices that are removed
mid-recording. Checks that:
  - unplugging the output (speakers/headphones) doesn't stop the recording
    and the app ends up on a working output after Stop
  - unplugging the microphone doesn't stop the recording; the app sound is
    kept for the whole length and the mic up to the unplug, and no other
    input (like a speaker monitor) takes the mic's place
  - nothing is left behind (record_sink, loopbacks, mic files)
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import wave

import numpy as np

sys.path.insert(0, os.environ.get("APP_DIR", os.getcwd()))
from audio import SystemAudioCapture  # noqa: E402

results = []
RATE = 48000


def record(name, ok, detail=""):
    results.append((name, ok, detail))


def pactl(*args, check=True):
    return subprocess.run(
        ["pactl", *args], capture_output=True, text=True, check=check
    ).stdout


def names(kind):
    return [line.split("\t")[1] for line in pactl("list", "short", kind).splitlines()]


def tone(path, freq, seconds):
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=f={freq}:duration={seconds}", "-af", "volume=-12dB",
         "-ar", str(RATE), "-ac", "2", path],
        check=True,
    )


def level_per_second(path, freq):
    """Strength of `freq` in each second of a WAV file."""
    with wave.open(path) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        rate, channels = w.getframerate(), w.getnchannels()
    mono = data.reshape(-1, channels).mean(axis=1)
    levels = []
    for start in range(0, len(mono) - rate + 1, rate):
        chunk = mono[start:start + rate]
        t = np.arange(len(chunk)) / rate
        levels.append(abs(np.dot(chunk, np.exp(-2j * np.pi * freq * t))) / len(chunk))
    return levels


def leftovers():
    mods = pactl("list", "short", "modules")
    return [line for line in mods.splitlines()
            if "record_sink" in line]


def run_with_timeout(fn, seconds):
    """Run fn in a thread; return (finished, error)."""
    box = {}

    def target():
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    return not t.is_alive(), box.get("error")


def app_sink():
    """Sink the test app (client 'apptone') is playing to."""
    sinks = {line.split("\t")[0]: line.split("\t")[1]
             for line in pactl("list", "short", "sinks").splitlines()}
    out = pactl("list", "sink-inputs")
    for block in out.split("Sink Input #")[1:]:
        if 'application.name = "apptone"' in block:
            for line in block.splitlines():
                if line.strip().startswith("Sink:"):
                    return sinks.get(line.split(":", 1)[1].strip())
    return None


def output_unplugged(tmp, app_tone):
    """The default output (the 'USB interface') disappears mid-recording."""
    usb = pactl("load-module", "module-null-sink", "sink_name=usb_interface").strip()
    pactl("set-default-sink", "usb_interface")
    player = subprocess.Popen(["paplay", "--client-name=apptone", app_tone])
    time.sleep(0.5)

    out = os.path.join(tmp, "out_unplug.wav")
    capture = SystemAudioCapture(["apptone"])
    capture.setup()
    capture.start_recording(out)
    time.sleep(1.5)
    pactl("unload-module", usb)  # unplug
    time.sleep(2.0)

    finished, error = run_with_timeout(lambda: (capture.stop_recording(),
                                                capture.cleanup()), 15)
    record("output unplugged: Stop finishes", finished and error is None,
           f"error: {error}" if error else "")

    levels = level_per_second(out, 440)
    record("output unplugged: recording keeps the app sound after the unplug",
           len(levels) >= 3 and min(levels[:3]) > 50,
           f"440 Hz level per second: {[round(v) for v in levels]}")
    sink = app_sink()
    record("output unplugged: app plays on a working output after Stop",
           sink is not None and sink != "record_sink" and sink in names("sinks"),
           f"app is on: {sink}")
    record("output unplugged: nothing left behind", not leftovers(), f"{leftovers()}")
    player.kill()


def mic_unplugged(tmp, app_tone, mic_tone):
    """The microphone disappears mid-recording."""
    pactl("set-default-sink", "test_speakers")
    feed = pactl("load-module", "module-null-sink", "sink_name=usb_mic_feed").strip()
    mic = pactl("load-module", "module-virtual-source", "source_name=usb_mic",
                "master=usb_mic_feed.monitor").strip()
    pactl("set-default-source", "usb_mic")
    player = subprocess.Popen(["paplay", "--client-name=apptone", app_tone])
    talker = subprocess.Popen(["paplay", "--client-name=voice",
                               "--device=usb_mic_feed", mic_tone])
    time.sleep(0.5)

    out = os.path.join(tmp, "mic_unplug.wav")
    capture = SystemAudioCapture(["apptone"], record_microphone=True)
    capture.setup()
    capture.start_recording(out)
    mic_file = capture.mic_file
    time.sleep(2.0)
    pactl("unload-module", mic)  # unplug the mic
    pactl("unload-module", feed)
    time.sleep(2.0)

    finished, error = run_with_timeout(lambda: (capture.stop_recording(),
                                                capture.cleanup()), 15)
    record("mic unplugged: Stop finishes", finished and error is None,
           f"error: {error}" if error else "")
    app_levels = level_per_second(out, 440)
    mic_levels = level_per_second(out, 1000)
    record("mic unplugged: app sound kept for the whole recording",
           len(app_levels) >= 4 and min(app_levels[:4]) > 50,
           f"440 Hz per second: {[round(v) for v in app_levels]}")
    # The fake mic can take a moment to wake up, so look for the voice
    # anywhere before the unplug (2 s in), and for nothing after it.
    record("mic unplugged: voice recorded up to the unplug",
           len(mic_levels) >= 3 and max(mic_levels[:3]) > 50,
           f"1000 Hz per second: {[round(v) for v in mic_levels]}")
    record("mic unplugged: no other input takes the mic's place",
           len(app_levels) >= 4 and app_levels[-1] < app_levels[0] * 1.3
           and (mic_levels[-1] if mic_levels else 0) < 50,
           f"440 Hz {[round(v) for v in app_levels]}, "
           f"1000 Hz {[round(v) for v in mic_levels]}")
    record("mic unplugged: no mic file left behind",
           bool(mic_file) and not os.path.exists(mic_file), f"{mic_file}")
    record("mic unplugged: nothing left behind", not leftovers(), f"{leftovers()}")
    player.kill()
    talker.kill()


def main():
    tmp = tempfile.mkdtemp()
    app_tone = os.path.join(tmp, "app.wav")
    mic_tone = os.path.join(tmp, "mic.wav")
    tone(app_tone, 440, 30)
    tone(mic_tone, 1000, 30)
    if "test_speakers" not in names("sinks"):  # the "laptop speakers"
        pactl("load-module", "module-null-sink", "sink_name=test_speakers")

    output_unplugged(tmp, app_tone)
    mic_unplugged(tmp, app_tone, mic_tone)


try:
    main()
except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    record("unplug test", False, str(e).strip().splitlines()[-1])

for name, ok, detail in results:
    line = f"{'PASS' if ok else 'FAIL'} {name}"
    if detail:
        line += f" ({detail})" if ok else f": {detail}"
    print(line, flush=True)
sys.exit(0 if results and all(ok for _, ok, _ in results) else 1)
