"""Audio capture functionality for the screen recorder."""

import logging
import os
import signal
import subprocess
import time
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


class SystemAudioCapture:
    """Handles system audio capture using PulseAudio."""

    def __init__(
        self,
        selected_apps: list[str],
        sample_rate: int = 48000,
        channels: int = 2,
        log_callback: Callable[[str], None] | None = None,
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

            # Move existing apps to record sink
            moved_count = self.move_apps_to_record_sink()

            self.is_setup = True
            self.callback_logger.info(
                f"Audio setup complete. Moved {moved_count} applications."
            )
            return True

        except AudioCaptureError as e:
            self.callback_logger.error(f"Audio setup failed: {e}")
            self.cleanup()  # Clean up any partial setup
            return False

    def start_recording(self, output_file: str) -> subprocess.Popen | None:
        """Start audio recording."""
        if not self.is_setup:
            raise AudioCaptureError("Audio capture not set up. Call setup() first.")

        try:
            # Use WAV format for better compatibility and no compression issues
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "pulse",
                "-i",
                "record_sink.monitor",
                "-c:a",
                "pcm_s16le",  # Uncompressed PCM audio
                "-ar",
                str(self.sample_rate),
                "-ac",
                str(self.channels),
                output_file,
            ]

            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )

            # Quick check if process started successfully
            time.sleep(0.5)
            if proc.poll() is not None:
                stderr = proc.stderr.read() if proc.stderr else "Unknown error"
                raise AudioCaptureError(f"FFmpeg failed to start: {stderr}")

            self.recording_process = proc
            self.paused = False
            self.callback_logger.info("Audio recording started")
            return proc

        except Exception as e:
            raise AudioCaptureError(f"Failed to start audio recording: {e}")

    def pause_recording(self) -> None:
        """Pause/resume audio recording by sending SIGSTOP/SIGCONT to ffmpeg process."""
        if not self.recording_process:
            raise AudioCaptureError("No recording process to pause")

        try:
            if self.paused:
                # Resume recording
                self.recording_process.send_signal(signal.SIGCONT)
                self.paused = False
                self.callback_logger.info("Audio recording resumed")
            else:
                # Pause recording
                self.recording_process.send_signal(signal.SIGSTOP)
                self.paused = True
                self.callback_logger.info("Audio recording paused")
        except Exception as e:
            self.callback_logger.error(f"Failed to pause/resume audio recording: {e}")
            raise AudioCaptureError(f"Failed to pause/resume audio recording: {e}")

    def stop_recording(self) -> None:
        """Stop the audio recording process."""
        if self.recording_process:
            try:
                # If paused, resume first so it can terminate properly
                if self.paused:
                    self.callback_logger.info(
                        "Resuming paused audio recording before stopping"
                    )
                    self.recording_process.send_signal(signal.SIGCONT)
                    time.sleep(0.2)

                self.callback_logger.info("Sending SIGTERM to audio recording process")
                # Send SIGTERM first (gentler termination)
                self.recording_process.terminate()

                # Wait longer for audio recording to flush buffers properly
                try:
                    self.recording_process.wait(timeout=8)
                    self.callback_logger.info("Audio recording stopped gracefully")
                except subprocess.TimeoutExpired:
                    self.callback_logger.warning(
                        "Audio recording process did not terminate gracefully within 8 seconds, killing it"
                    )
                    self.recording_process.kill()
                    try:
                        self.recording_process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.callback_logger.error(
                            "Audio recording process could not be killed"
                        )

            except Exception as e:
                self.callback_logger.error(f"Error stopping audio recording: {e}")
                # Try to kill the process as a last resort
                try:
                    if self.recording_process:
                        self.recording_process.kill()
                except Exception:
                    pass
            finally:
                self.recording_process = None
                self.paused = False

    def cleanup(self) -> bool:
        """Clean up audio capture environment."""
        success = True

        try:
            # No need to restore default sink since we never changed it
            self.callback_logger.debug("Default sink unchanged - no restoration needed")

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
