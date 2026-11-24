"""Audio capture functionality for the screen recorder."""

import logging
import os
import subprocess
import threading
import time
import wave
from collections import deque
from collections.abc import Callable

from utils import CallbackLogger


class AudioCaptureError(Exception):
    """Custom exception for audio capture errors."""

    pass


SNAP_CONNECT_AUDIO_CMD = "sudo snap connect screenrec-lite:audio-record"


def snap_audio_record_missing() -> bool:
    """True if running as a snap without the audio-record permission.

    Without it the snap can play sound but not capture it, so recordings
    would be silent. Outside a snap (or if the check itself fails) assume
    recording is allowed.
    """
    if not os.environ.get("SNAP"):
        return False
    try:
        result = subprocess.run(
            ["snapctl", "is-connected", "audio-record"],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    # Exit code 1 means "not connected"; anything else is treated as unknown.
    return result.returncode == 1


class _ParecStream:
    """Records one PulseAudio/PipeWire source to a WAV file with parec.

    parec streams raw audio with a small buffer and this class writes the
    WAV file itself. Knowing when each chunk arrives gives the real time of
    the first sample (start_time), used to line recordings up.
    """

    # How often a watched device is checked, and how much audio is held back
    # before writing so the part recorded after it vanished can be dropped.
    WATCH_INTERVAL = 0.2
    HOLD_BACK = 0.5

    def __init__(self, device: str, output_file: str, sample_rate: int,
                 channels: int, logger: CallbackLogger, watch: bool = False):
        self.device = device
        self.output_file = output_file
        self.sample_rate = sample_rate
        self.channels = channels
        self.log = logger
        self.proc: subprocess.Popen | None = None
        self.start_time: float | None = None
        self.paused = False
        self._writer: threading.Thread | None = None
        self._stderr_tail: deque[str] = deque(maxlen=20)
        # watch: stop if the device disappears (USB unplugged). Otherwise the
        # sound server moves the recording to another input, which can be a
        # speaker monitor and would record the app sound a second time.
        self.watch = watch
        self.lost = False

    def start(self, wait: float = 5.0) -> None:
        cmd = [
            "parec",
            f"--device={self.device}",
            "--raw",
            "--format=s16le",
            f"--rate={self.sample_rate}",
            f"--channels={self.channels}",
            "--latency-msec=20",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        wav = wave.open(self.output_file, "wb")
        wav.setnchannels(self.channels)
        wav.setsampwidth(2)
        wav.setframerate(self.sample_rate)
        self._writer = threading.Thread(
            target=self._write_audio, args=(proc, wav), daemon=True
        )
        self._writer.start()
        threading.Thread(target=self._read_errors, args=(proc,), daemon=True).start()
        self.proc = proc
        if self.watch:
            threading.Thread(target=self._watch_device, args=(proc,), daemon=True).start()

        # Wait until audio actually flows (or parec fails)
        deadline = time.monotonic() + wait
        while self.start_time is None and time.monotonic() < deadline:
            if proc.poll() is not None:
                time.sleep(0.1)  # let the reader collect the error message
                stderr = "\n".join(self._stderr_tail) or "Unknown error"
                raise AudioCaptureError(f"parec failed to start: {stderr}")
            time.sleep(0.01)
        if self.start_time is None and wait > 0:
            self.log.warning(f"No audio data yet from {self.device} after {wait:.0f} s")

    def _write_audio(self, proc: subprocess.Popen, wav: "wave.Wave_write") -> None:
        """Copy parec's audio into the WAV file until parec exits.

        Each sample's time is first_sample_time + index / rate. Every chunk
        that arrives gives an upper bound for first_sample_time (it can't
        have arrived before it was recorded); the smallest one over the
        first second is the closest estimate. While paused, audio is read
        and thrown away.
        """
        bytes_per_frame = 2 * self.channels
        frames_read = 0
        pending = b""
        # Watched devices: keep the newest HOLD_BACK seconds unwritten, so
        # they can be dropped if the device turns out to have vanished.
        hold_bytes = (
            int(self.HOLD_BACK * self.sample_rate) * bytes_per_frame if self.watch else 0
        )
        held = bytearray()
        fd = proc.stdout.fileno()
        try:
            while True:
                data = os.read(fd, 65536)
                if not data:
                    break
                now = time.time()
                data = pending + data
                usable = len(data) - len(data) % bytes_per_frame
                data, pending = data[:usable], data[usable:]
                frames_read += usable // bytes_per_frame
                if frames_read <= self.sample_rate:  # first second
                    estimate = now - frames_read / self.sample_rate
                    if self.start_time is None or estimate < self.start_time:
                        self.start_time = estimate
                if self.paused or self.lost:
                    continue
                held += data
                if len(held) > hold_bytes:
                    cut = len(held) - hold_bytes
                    wav.writeframes(bytes(held[:cut]))
                    del held[:cut]
            if held and not self.lost:
                wav.writeframes(bytes(held))
        except Exception as e:
            self.log.error(f"Audio writing failed: {e}")
        finally:
            wav.close()

    def _watch_device(self, proc: subprocess.Popen) -> None:
        """Stop recording if the device disappears (e.g. USB unplugged)."""
        while proc.poll() is None:
            time.sleep(self.WATCH_INTERVAL)
            try:
                out = subprocess.run(
                    ["pactl", "list", "short", "sources"],
                    capture_output=True, text=True, timeout=5,
                ).stdout
            except (OSError, subprocess.TimeoutExpired):
                continue
            present = any(
                len(parts) > 1 and parts[1] == self.device
                for parts in (line.split("\t") for line in out.splitlines())
            )
            if not present and proc.poll() is None:
                self.lost = True
                self.log.warning(f"{self.device} was disconnected; its recording stops here")
                proc.terminate()
                return

    def _read_errors(self, proc: subprocess.Popen) -> None:
        """Keep parec's last messages for error reports."""
        for line in proc.stderr:
            self._stderr_tail.append(line.decode(errors="replace").rstrip())

    def stop(self) -> None:
        proc = self.proc
        if not proc:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.log.warning("parec didn't stop, killing it")
                proc.kill()
                proc.wait(timeout=2)
            if self._writer:
                self._writer.join(timeout=5)
        finally:
            self.proc = None
            self.paused = False


class SystemAudioCapture:
    """Handles system audio capture using PulseAudio."""

    def __init__(
        self,
        selected_apps: list[str],
        sample_rate: int = 48000,
        channels: int = 2,
        log_callback: Callable[[str], None] | None = None,
        record_microphone: bool = False,
    ):
        self.null_sink_module: str | None = None
        self.loopback_module: str | None = None
        self.original_default_sink: str | None = None
        self.apps_to_capture = selected_apps
        self.sample_rate = sample_rate
        self.channels = channels
        self.is_setup = False
        self.recording_process: subprocess.Popen | None = None
        self.paused = False
        # Wall-clock time (time.time()) of the first recorded audio sample,
        # measured while recording. Used to line the audio up with the video.
        self.start_time: float | None = None
        self._app_stream: _ParecStream | None = None
        # Microphone: recorded separately (never through record_sink, whose
        # loopback would play your voice back on the speakers) and mixed in
        # when the recording stops.
        self.record_microphone = record_microphone
        self.mic_source: str | None = None
        self._mic_stream: _ParecStream | None = None
        self.mic_file: str | None = None

        # Set up logging
        self.logger = logging.getLogger(__name__)
        self.callback_logger = CallbackLogger(self.logger, log_callback)

    def _run_pactl_command(
        self, cmd: list[str], timeout: int = 10
    ) -> subprocess.CompletedProcess:
        """Run a pactl command with proper error handling."""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=True
            )
            self.logger.debug(f"Command succeeded: {' '.join(cmd)}")
            return result
        except subprocess.TimeoutExpired:
            raise AudioCaptureError(f"Command timed out: {' '.join(cmd)}")
        except subprocess.CalledProcessError as e:
            raise AudioCaptureError(f"Command failed: {' '.join(cmd)} - {e.stderr}")
        except FileNotFoundError:
            raise AudioCaptureError(
                "pactl command not found. Please install PulseAudio."
            )

    def _check_pulseaudio_running(self) -> bool:
        """Check if PulseAudio is running."""
        try:
            self._run_pactl_command(["pactl", "info"])
            return True
        except AudioCaptureError:
            return False

    def get_real_sink(self) -> str:
        """Get the real audio sink (not our virtual sink)."""
        try:
            result = self._run_pactl_command(["pactl", "list", "short", "sinks"])
            sinks = result.stdout.strip().splitlines()

            for line in sinks:
                if "record_sink" not in line and line.strip():
                    return line.split("\t")[1]

            raise AudioCaptureError("No real audio sink found")

        except AudioCaptureError as e:
            self.callback_logger.error(f"Failed to get real sink: {e}")
            raise

    def get_sink_inputs(self) -> list[dict]:
        """Get list of current sink inputs with their properties."""
        try:
            result = self._run_pactl_command(["pactl", "list", "sink-inputs"])
            inputs = []

            for block in result.stdout.split("Sink Input #")[
                1:
            ]:  # Skip first empty block
                lines = block.strip().splitlines()
                if not lines:
                    continue

                # Extract sink input ID
                sink_input_id = lines[0].strip()

                # Extract application name
                app_name = None
                for line in lines:
                    if "application.name = " in line:
                        app_name = line.split('application.name = "')[1].split('"')[0]
                        break

                if app_name:
                    inputs.append({"id": sink_input_id, "app_name": app_name})

            return inputs

        except AudioCaptureError as e:
            self.callback_logger.error(f"Failed to get sink inputs: {e}")
            return []

    def move_apps_back(self) -> int:
        """Move every stream playing into record_sink to the real output."""
        try:
            sinks = self._run_pactl_command(["pactl", "list", "short", "sinks"])
            record_ids = [
                line.split("\t")[0]
                for line in sinks.stdout.splitlines()
                if len(line.split("\t")) > 1 and line.split("\t")[1] == "record_sink"
            ]
            if not record_ids:
                return 0
            target = self.original_default_sink or self.get_real_sink()
            inputs = self._run_pactl_command(["pactl", "list", "short", "sink-inputs"])
        except AudioCaptureError as e:
            self.callback_logger.warning(f"Couldn't move apps back: {e}")
            return 0

        moved = 0
        for line in inputs.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) > 1 and parts[1] in record_ids:
                try:
                    self._run_pactl_command(
                        ["pactl", "move-sink-input", parts[0], target]
                    )
                    moved += 1
                except AudioCaptureError as e:
                    self.callback_logger.warning(f"Couldn't move stream {parts[0]}: {e}")
        if moved:
            self.callback_logger.info(f"Moved {moved} app(s) back to {target}")
        return moved

    def move_apps_to_record_sink(self) -> int:
        """Move selected applications to the record sink."""
        moved_count = 0
        sink_inputs = self.get_sink_inputs()

        for sink_input in sink_inputs:
            for app in self.apps_to_capture:
                if app.lower() in sink_input["app_name"].lower():
                    try:
                        self._run_pactl_command(
                            [
                                "pactl",
                                "move-sink-input",
                                sink_input["id"],
                                "record_sink",
                            ]
                        )
                        self.callback_logger.info(
                            f"Moved {sink_input['app_name']} to record sink"
                        )
                        moved_count += 1
                        break
                    except AudioCaptureError as e:
                        self.callback_logger.warning(
                            f"Failed to move {sink_input['app_name']}: {e}"
                        )

        if moved_count == 0:
            self.callback_logger.warning("No matching applications found to capture")

        return moved_count

    def _remove_stale_modules(self) -> None:
        """Unload record_sink modules left behind by an earlier run."""
        try:
            result = self._run_pactl_command(["pactl", "list", "short", "modules"])
        except AudioCaptureError:
            return
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            module_id, name, args = parts[0], parts[1], parts[2]
            stale = (
                name == "module-null-sink" and "sink_name=record_sink" in args
            ) or (name == "module-loopback" and "source=record_sink.monitor" in args)
            if stale:
                try:
                    self._run_pactl_command(["pactl", "unload-module", module_id])
                    self.callback_logger.info(
                        f"Removed leftover audio routing from a previous run ({name})"
                    )
                except AudioCaptureError as e:
                    self.callback_logger.warning(f"Could not remove {name}: {e}")

    def setup(self) -> bool:
        """Set up audio capture environment."""
        try:
            if not self._check_pulseaudio_running():
                raise AudioCaptureError("PulseAudio is not running")

            self.callback_logger.info("Setting up audio capture...")

            # A previous run that crashed or was killed can leave its sink and
            # loopback loaded; a second loopback plays everything twice (+6 dB).
            self._remove_stale_modules()

            # Store original default sink
            result = self._run_pactl_command(["pactl", "get-default-sink"])
            self.original_default_sink = result.stdout.strip()
            self.callback_logger.debug(f"Original sink: {self.original_default_sink}")

            # Create null sink for recording
            result = self._run_pactl_command(
                [
                    "pactl",
                    "load-module",
                    "module-null-sink",
                    "sink_name=record_sink",
                    f"rate={self.sample_rate}",
                    f"channels={self.channels}",
                    "sink_properties=device.description=RecordSink",
                ]
            )
            self.null_sink_module = result.stdout.strip()
            self.callback_logger.debug(
                f"Created null sink module: {self.null_sink_module}"
            )

            # Get real sink for loopback
            real_sink = self.get_real_sink()
            self.callback_logger.debug(f"Real sink: {real_sink}")

            # Create loopback from record sink to the original default sink (Focusrite)
            result = self._run_pactl_command(
                [
                    "pactl",
                    "load-module",
                    "module-loopback",
                    "source=record_sink.monitor",
                    f"sink={self.original_default_sink}",
                    "latency_msec=50",
                ]
            )
            self.loopback_module = result.stdout.strip()
            self.callback_logger.debug(
                f"Created loopback module: {self.loopback_module}"
            )

            # Don't change the default sink - keep using Focusrite for output
            self.callback_logger.info(
                f"Keeping {self.original_default_sink} as default output"
            )

            if self.record_microphone:
                self.mic_source = self.get_microphone()
                if self.mic_source:
                    self.callback_logger.info(f"Microphone: {self.mic_source}")
                else:
                    self.callback_logger.warning(
                        "No microphone found; recording without it"
                    )

            # Move existing apps to record sink
            moved_count = (
                self.move_apps_to_record_sink() if self.apps_to_capture else 0
            )

            self.is_setup = True
            self.callback_logger.info(
                f"Audio setup complete. Moved {moved_count} applications."
            )
            return True

        except AudioCaptureError as e:
            self.callback_logger.error(f"Audio setup failed: {e}")
            self.cleanup()  # Clean up any partial setup
            return False

    def get_microphone(self) -> str | None:
        """The default input device, or None if there's no real microphone."""
        try:
            source = self._run_pactl_command(["pactl", "get-default-source"]).stdout.strip()
        except AudioCaptureError as e:
            self.callback_logger.warning(f"Couldn't find the microphone: {e}")
            return None
        # With no microphone the default source is a speaker monitor or our
        # own record_sink; recording that would duplicate the app sound.
        if not source or source.endswith(".monitor") or "record_sink" in source:
            return None
        return source

    def start_recording(self, output_file: str) -> subprocess.Popen | None:
        """Start recording record_sink (and the microphone) to WAV files."""
        if not self.is_setup:
            raise AudioCaptureError("Audio capture not set up. Call setup() first.")

        self.start_time = None
        self.paused = False
        try:
            stream = _ParecStream(
                "record_sink.monitor", output_file, self.sample_rate,
                self.channels, self.callback_logger,
            )
            stream.start()
            self._app_stream = stream
            self.start_time = stream.start_time
            self.recording_process = stream.proc
            self.callback_logger.info("Audio recording started")
        except AudioCaptureError:
            raise
        except Exception as e:
            raise AudioCaptureError(f"Failed to start audio recording: {e}")

        if self.mic_source:
            self.mic_file = str(output_file).removesuffix(".wav") + "_mic.wav"
            mic = _ParecStream(
                self.mic_source, self.mic_file, self.sample_rate,
                self.channels, self.callback_logger, watch=True,
            )
            try:
                # Wait until the mic sends sound: right after waking up some
                # inputs deliver a burst of delayed audio, which would throw
                # off its timing. Capped so a slow mic can't hold up the video.
                mic.start(wait=2.0)
                self._mic_stream = mic
                self.callback_logger.info("Microphone recording started")
            except Exception as e:  # noqa: BLE001
                # The recording goes on without the mic rather than failing
                self.callback_logger.warning(f"Microphone recording failed: {e}")
                mic.stop()
                self._remove_mic_file()
        return self.recording_process

    def pause_recording(self) -> None:
        """Pause or resume the audio recording."""
        if not self.recording_process:
            raise AudioCaptureError("No recording process to pause")
        # Video pausing only skips frames, so audio does the same: parec keeps
        # running and the audio it sends while paused is dropped.
        self.paused = not self.paused
        for stream in (self._app_stream, self._mic_stream):
            if stream:
                stream.paused = self.paused
        self.callback_logger.info(
            "Audio recording paused" if self.paused else "Audio recording resumed"
        )

    def stop_recording(self, mix_microphone: bool = True) -> None:
        """Stop recording and finish the WAV file (with the mic mixed in)."""
        try:
            for stream in (self._app_stream, self._mic_stream):
                if stream:
                    stream.stop()
            if self._app_stream:
                # The first-sample estimate keeps improving for a second
                self.start_time = self._app_stream.start_time
                self.callback_logger.info("Audio recording stopped")
        except Exception as e:
            self.callback_logger.error(f"Error stopping audio recording: {e}")
        finally:
            self.recording_process = None
            self.paused = False

        if self._mic_stream:
            if self._mic_stream.start_time is None:
                tail = "; ".join(self._mic_stream._stderr_tail)
                self.callback_logger.warning(
                    f"The microphone sent no sound; recording without it {tail}".strip()
                )
                self._remove_mic_file()
            elif mix_microphone and self._app_stream:
                self._mix_microphone()
            else:
                self._remove_mic_file()
        self._app_stream = None
        self._mic_stream = None

    def _remove_mic_file(self) -> None:
        if self.mic_file:
            try:
                os.remove(self.mic_file)
            except OSError:
                pass
        self.mic_file = None

    def _mix_microphone(self) -> None:
        """Mix the mic recording into the app recording, lined up in time.

        If mixing fails the mic recording is kept as <audio>_mic.wav so
        nothing is lost.
        """
        app_file = self._app_stream.output_file
        app_start = self._app_stream.start_time
        mic_start = self._mic_stream.start_time
        if app_start is None:
            offset = 0.0
            self.callback_logger.warning("Couldn't time the app sound; mixing unaligned")
        else:
            offset = mic_start - app_start  # > 0: the mic started later
        if offset >= 0.001:
            mic_chain = f"[1:a]adelay={offset * 1000:.0f}:all=1[mic]"
        elif offset <= -0.001:
            mic_chain = f"[1:a]atrim=start={-offset:.3f},asetpts=PTS-STARTPTS[mic]"
        else:
            mic_chain = "[1:a]anull[mic]"
        mixed = app_file.removesuffix(".wav") + "_mixed.wav"
        cmd = [
            "ffmpeg", "-y", "-nostdin", "-nostats", "-loglevel", "error",
            "-i", app_file, "-i", self.mic_file,
            "-filter_complex",
            # duration=first: the app recording is the timing reference.
            # normalize=0: keep both at their own volume instead of halving.
            f"{mic_chain};[0:a][mic]amix=inputs=2:duration=first:"
            "dropout_transition=0:normalize=0[out]",
            "-map", "[out]", "-c:a", "pcm_s16le", mixed,
        ]
        self.callback_logger.info(
            f"Mixing in the microphone (offset {offset * 1000:.0f} ms)..."
        )
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                raise AudioCaptureError(result.stderr.strip()[-300:])
            os.replace(mixed, app_file)
            self.callback_logger.info("Microphone mixed in")
            self._remove_mic_file()
        except Exception as e:  # noqa: BLE001
            self.callback_logger.error(
                f"Couldn't mix in the microphone ({e}); kept it as {self.mic_file}"
            )
            try:
                os.remove(mixed)
            except OSError:
                pass

    def cleanup(self) -> bool:
        """Clean up audio capture environment."""
        success = True

        try:
            # Move the apps back to the real output *before* removing the
            # virtual sink. If the sink disappears under them, apps see their
            # audio device vanish and some pause playback (YouTube in Chrome).
            self.move_apps_back()

            # Unload loopback module
            if self.loopback_module:
                try:
                    self._run_pactl_command(
                        ["pactl", "unload-module", self.loopback_module]
                    )
                    self.callback_logger.debug("Unloaded loopback module")
                except AudioCaptureError as e:
                    self.callback_logger.warning(
                        f"Failed to unload loopback module: {e}"
                    )
                    success = False

            # Unload null sink module
            if self.null_sink_module:
                try:
                    self._run_pactl_command(
                        ["pactl", "unload-module", self.null_sink_module]
                    )
                    self.callback_logger.debug("Unloaded null sink module")
                except AudioCaptureError as e:
                    self.callback_logger.warning(
                        f"Failed to unload null sink module: {e}"
                    )
                    success = False

            # Reset state
            self.null_sink_module = None
            self.loopback_module = None
            self.original_default_sink = None
            self.is_setup = False

            if success:
                self.callback_logger.info("Audio cleanup completed successfully")
            else:
                self.callback_logger.warning("Audio cleanup completed with some errors")

            return success

        except Exception as e:
            self.callback_logger.error(f"Unexpected error during cleanup: {e}")
            return False

    def get_available_applications(self) -> list[str]:
        """Get list of currently running applications that can be captured."""
        try:
            sink_inputs = self.get_sink_inputs()
            return [si["app_name"] for si in sink_inputs]
        except Exception as e:
            self.callback_logger.error(f"Failed to get available applications: {e}")
            return []
