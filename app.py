"""
Enhanced Screen Recorder Application

A feature-rich screen recording application with system audio capture,
modular architecture, comprehensive error handling, and advanced UI controls.

Features:
- Multi-monitor support with screen selection
- System audio capture from selected applications
- Configurable video codecs and quality settings
- Progress monitoring and recording statistics
- Keyboard shortcuts and enhanced UI
- Settings persistence and configuration management
- Comprehensive logging and error handling
"""

import logging
import sys
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

try:
    from ui import main
except ImportError as e:
    # Fallback to basic functionality if enhanced modules aren't available
    logging.error(f"Failed to import enhanced modules: {e}")
    print("Running in basic mode...")

    # Import original functionality as fallback
    import os
    import subprocess
    import threading
    import time
    import tkinter as tk
    from tkinter import messagebox, ttk

    import cv2
    import snap_compat  # noqa: F401  # before mss: fixes X11 libs in the snap
    import mss
    import numpy as np

    # Include original classes for fallback
    class SystemAudioCapture:
        def __init__(self, selected_apps: list[str], log_callback=None):
            self.null_sink_module: str | None = None
            self.loopback_module: str | None = None
            self.original_default_sink: str | None = None
            self.apps_to_capture = selected_apps
            self.log = log_callback if log_callback else print

        def get_real_sink(self) -> str:
            sinks = (
                subprocess.check_output(["pactl", "list", "short", "sinks"])
                .decode()
                .splitlines()
            )
            for line in sinks:
                if "record_sink" not in line:
                    return line.split("\t")[1]
            raise RuntimeError("No real audio sink found.")

        def move_apps_to_record_sink(self) -> None:
            inputs = subprocess.check_output(["pactl", "list", "sink-inputs"]).decode()
            for block in inputs.split("Sink Input #"):
                for app in self.apps_to_capture:
                    if f'application.name = "{app}"' in block:
                        lines = block.strip().splitlines()
                        sink_input_id = None
                        for line in lines:
                            if line.strip().startswith("Sink Input"):
                                sink_input_id = line.strip().split("#")[-1]
                                break
                        if not sink_input_id:
                            sink_input_id = lines[0].strip()
                        subprocess.run(
                            ["pactl", "move-sink-input", sink_input_id, "record_sink"]
                        )
                        self.log(
                            f"[AUDIO] Moved {app} (Sink Input #{sink_input_id}) to record_sink"
                        )
                        break

        def setup(self) -> None:
            self.original_default_sink = (
                subprocess.check_output(["pactl", "get-default-sink"]).decode().strip()
            )

            self.null_sink_module = (
                subprocess.check_output(
                    [
                        "pactl",
                        "load-module",
                        "module-null-sink",
                        "sink_name=record_sink",
                        "rate=48000",
                        "channels=2",
                        "sink_properties=device.description=RecordSink",
                    ]
                )
                .decode()
                .strip()
            )

            real_sink = self.get_real_sink()

            self.loopback_module = (
                subprocess.check_output(
                    [
                        "pactl",
                        "load-module",
                        "module-loopback",
                        "source=record_sink.monitor",
                        f"sink={real_sink}",
                        "latency_msec=50",
                    ]
                )
                .decode()
                .strip()
            )

            subprocess.run(["pactl", "set-default-sink", "record_sink"])
            self.move_apps_to_record_sink()

        def start_recording(self, output_file: str) -> subprocess.Popen:
            return subprocess.Popen(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "pulse",
                    "-i",
                    "record_sink.monitor",
                    "-c:a",
                    "pcm_s16le",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    output_file,
                ]
            )

        def cleanup(self) -> None:
            if self.original_default_sink:
                subprocess.run(
                    ["pactl", "set-default-sink", self.original_default_sink]
                )
            if self.loopback_module:
                subprocess.run(["pactl", "unload-module", self.loopback_module])
            if self.null_sink_module:
                subprocess.run(["pactl", "unload-module", self.null_sink_module])

    def merge_audio_video(video_path: str, audio_path: str, output_path: str) -> bool:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-i",
                audio_path,
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                output_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    def get_incremental_filename(base_name="output", extension=".mp4") -> str:
        i = 1
        while os.path.exists(f"{base_name}_{i}{extension}"):
            i += 1
        return f"{base_name}_{i}{extension}"

    class ScreenRecorder:
        def __init__(self, fps: float, monitor_index: int = 1):
            self.fps = fps
            self.monitor_index = monitor_index
            self.recording = False
            self.output_file = f"screen_{int(time.time())}.avi"

        def _get_screen_size(self) -> tuple[int, int]:
            with mss.mss() as sct:
                mon = sct.monitors[self.monitor_index]
                return mon["width"], mon["height"]

        def start_recording(self) -> str:
            self.recording = True
            screen_size = self._get_screen_size()
            out = cv2.VideoWriter(
                self.output_file, cv2.VideoWriter_fourcc(*"XVID"), self.fps, screen_size
            )
            delay = 1.0 / self.fps

            with mss.mss() as sct:
                monitor = sct.monitors[self.monitor_index]
                while self.recording:
                    frame_start = time.time()
                    frame = np.array(sct.grab(monitor))[:, :, :3]
                    out.write(frame)

                    # Ensure consistent frame timing
                    frame_time = time.time() - frame_start
                    sleep_time = max(0.0, delay - frame_time)
                    if sleep_time > 0:
                        time.sleep(sleep_time)

            out.release()
            return self.output_file

        def stop(self) -> None:
            self.recording = False

        def detect_screens(self) -> list[dict]:
            with mss.mss() as sct:
                monitors = sct.monitors
                return [
                    {
                        "width": monitor["width"],
                        "height": monitor["height"],
                        "left": monitor["left"],
                        "top": monitor["top"],
                    }
                    for monitor in monitors[1:]
                ]

    class RecorderApp:
        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.root.title("Screen + System Audio Recorder (Basic Mode)")
            self.root.configure(bg="#1e1e1e")

            self.recorder: ScreenRecorder | None = None
            self.audio_capture: SystemAudioCapture | None = None
            self.audio_proc: subprocess.Popen | None = None
            self.video_file: str | None = None
            self.audio_file = "audio.wav"
            self.final_file: str | None = None
            self.last_saved_recording: str | None = None

            self.app_checkboxes = {}
            self.app_list = [
                "Firefox",
                "Chrome",
                "chromium",
                "zoom",
                "Spotify",
                "discord",
                "obs",
                "brave",
            ]

            self.log_text = tk.StringVar(value="Ready - Running in basic mode")

            self._build_ui()
            self.screens = ScreenRecorder(1).detect_screens()
            self.selected_screen_index = tk.IntVar(value=1)
            self._populate_screens()

        def _build_ui(self) -> None:
            label_style = {"bg": "#1e1e1e", "fg": "#ffffff"}
            entry_style = {
                "bg": "#2e2e2e",
                "fg": "#ffffff",
                "insertbackground": "white",
            }

            tk.Label(self.root, text="FPS:", **label_style).grid(
                row=0, column=0, sticky="w"
            )
            self.fps_entry = tk.Entry(self.root, **entry_style)
            self.fps_entry.insert(0, "20")
            self.fps_entry.grid(row=0, column=1, sticky="w")

            tk.Label(self.root, text="Capture Audio From:", **label_style).grid(
                row=2, column=0, columnspan=2, sticky="w"
            )
            for idx, app in enumerate(self.app_list):
                var = tk.BooleanVar(value=True)
                chk = tk.Checkbutton(
                    self.root,
                    text=app,
                    variable=var,
                    bg="#1e1e1e",
                    fg="#cccccc",
                    selectcolor="#2e2e2e",
                    activebackground="#333333",
                )
                chk.grid(row=3 + idx // 2, column=idx % 2, sticky="w")
                self.app_checkboxes[app] = var

            self.status = tk.Label(self.root, textvariable=self.log_text, **label_style)
            self.status.grid(
                row=3 + len(self.app_list) // 2,
                column=0,
                columnspan=2,
                pady=10,
                sticky="w",
            )

            btn_style = {"bg": "#3a3a3a", "fg": "white", "activebackground": "#5a5a5a"}
            tk.Button(
                self.root,
                text="Start Recording",
                command=self.start_recording,
                **btn_style,
            ).grid(row=4 + len(self.app_list) // 2, column=0)
            tk.Button(
                self.root,
                text="Stop Recording",
                command=self.stop_recording,
                **btn_style,
            ).grid(row=4 + len(self.app_list) // 2, column=1)

            tk.Button(
                self.root,
                text="Cancel Recording",
                command=self.cancel_recording,
                **btn_style,
            ).grid(row=5 + len(self.app_list) // 2, column=0)

            self.open_file_btn = tk.Button(
                self.root,
                text="📂 Open Recording",
                command=self.open_last_recording,
                **btn_style,
                state=tk.DISABLED,
            )
            self.open_file_btn.grid(
                row=5 + len(self.app_list) // 2, column=1, columnspan=1, pady=5
            )

        def _populate_screens(self):
            screen_options = [
                f"Screen {i + 1}: {s['width']}x{s['height']} at ({s['left']},{s['top']})"
                for i, s in enumerate(self.screens)
            ]
            tk.Label(self.root, text="Select Screen:", bg="#1e1e1e", fg="white").grid(
                row=1, column=0, sticky="w"
            )
            screen_menu = ttk.Combobox(
                self.root, values=screen_options, state="readonly"
            )
            screen_menu.current(0)
            screen_menu.grid(row=1, column=1, sticky="w")
            screen_menu.bind(
                "<<ComboboxSelected>>",
                lambda _: self.selected_screen_index.set(screen_menu.current() + 1),
            )

        def _log(self, msg: str) -> None:
            print(msg)
            self.log_text.set(msg)

        def start_recording(self) -> None:
            try:
                fps = float(self.fps_entry.get())
            except ValueError:
                messagebox.showerror("Invalid FPS", "Please enter a valid FPS value.")
                return

            selected_apps = [
                app for app, var in self.app_checkboxes.items() if var.get()
            ]
            self.recorder = ScreenRecorder(
                fps, monitor_index=self.selected_screen_index.get()
            )
            self.audio_capture = SystemAudioCapture(
                selected_apps, log_callback=self._log
            )
            self.audio_capture.setup()
            self.audio_proc = self.audio_capture.start_recording(self.audio_file)

            def _record():
                self.video_file = self.recorder.start_recording()

            threading.Thread(target=_record, daemon=True).start()
            self._log("Recording...")

        def stop_recording(self) -> None:
            if self.recorder:
                self.recorder.stop()
            if self.audio_proc:
                self.audio_proc.terminate()
                self.audio_proc.wait()
            if self.audio_capture:
                self.audio_capture.cleanup()

            if self.video_file:
                default_name = get_incremental_filename()
                success = merge_audio_video(
                    self.video_file, self.audio_file, default_name
                )
                if success:
                    self.final_file = default_name
                    self.last_saved_recording = default_name
                    self.open_file_btn.config(state=tk.NORMAL)
                    self._log(f"Recording saved as {self.final_file}")
                    for f in [self.video_file, self.audio_file]:
                        try:
                            os.remove(f)
                        except Exception as e:
                            self._log(f"Error deleting file {f}: {e}")
                else:
                    self._log("Failed to merge audio and video files.")

    def cancel_recording(self) -> None:
        """Cancel recording without saving."""
        if self.recorder:
            self.recorder.stop()
        if self.audio_proc:
            self.audio_proc.terminate()
            self.audio_proc.wait()
        if self.audio_capture:
            self.audio_capture.cleanup()

        # Clean up temp files without saving
        for f in [self.video_file, self.audio_file]:
            if f:
                try:
                    os.remove(f)
                except Exception as e:
                    self._log(f"Error deleting temp file {f}: {e}")

        self._log("Recording cancelled - no files saved")

    def open_last_recording(self) -> None:
        """Open the last saved recording file."""
        if not self.last_saved_recording:
            messagebox.showwarning("No Recording", "No recording file to open.")
            return

        if not os.path.exists(self.last_saved_recording):
            messagebox.showerror(
                "File Not Found",
                f"Recording file not found:\n{self.last_saved_recording}",
            )
            return

        try:
            # Open the file with the default system application
            import platform

            system = platform.system()
            if system == "Windows":
                os.startfile(self.last_saved_recording)
                self._log(
                    f"Opened recording: {os.path.basename(self.last_saved_recording)}"
                )
            elif system == "Darwin":  # macOS
                result = subprocess.run(
                    ["open", self.last_saved_recording],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    self._log(
                        f"Opened recording: {os.path.basename(self.last_saved_recording)}"
                    )
                else:
                    raise Exception(f"Command failed: {result.stderr}")
            else:  # Linux and other Unix-like systems
                # Try multiple methods for Linux
                methods = [
                    ["xdg-open", self.last_saved_recording],
                    ["gnome-open", self.last_saved_recording],
                    ["kde-open", self.last_saved_recording],
                    ["vlc", self.last_saved_recording],
                    ["mpv", self.last_saved_recording],
                ]

                success = False
                for method in methods:
                    try:
                        process = subprocess.Popen(
                            method,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        time.sleep(0.5)
                        if process.poll() is None or process.returncode == 0:
                            success = True
                            self._log(
                                f"Opened recording with {method[0]}: {os.path.basename(self.last_saved_recording)}"
                            )
                            break
                    except (FileNotFoundError, Exception):
                        continue

                if not success:
                    # Open folder as fallback
                    try:
                        subprocess.Popen(
                            [
                                "xdg-open",
                                os.path.dirname(self.last_saved_recording),
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        self._log(
                            f"Opened folder containing: {os.path.basename(self.last_saved_recording)}"
                        )
                    except Exception:
                        raise Exception(
                            f"Could not open file or folder: {self.last_saved_recording}"
                        )

        except Exception as e:
            self._log(f"Failed to open recording: {e}")
            messagebox.showerror(
                "Error",
                f"Failed to open recording automatically.\n\nFile: {self.last_saved_recording}\n\nError: {e}",
            )

    def main():
        """Fallback main function for basic mode."""
        root = tk.Tk(className="screenrec-lite")
        RecorderApp(root)
        root.mainloop()


if __name__ == "__main__":
    main()
