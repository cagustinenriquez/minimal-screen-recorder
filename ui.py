"""Enhanced UI for the screen recorder with better UX and controls."""

import os
import subprocess
import threading
import tkinter as tk
import urllib.parse
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from audio import (
    SNAP_CONNECT_AUDIO_CMD,
    AudioCaptureError,
    SystemAudioCapture,
    snap_audio_record_missing,
)
from config import ConfigManager
from utils import (
    RecordingTimer,
    check_dependencies,
    get_incremental_filename,
    setup_logging,
    validate_fps,
)
from video import (
    ScreenRecorder,
    VideoCaptureError,
    cleanup_temp_files,
    merge_audio_video,
)


class ProgressWindow:
    """Separate window showing recording progress."""

    def __init__(self, parent, timer: RecordingTimer):
        self.parent = parent
        self.timer = timer
        self.window = tk.Toplevel(parent)
        self.window.title("Recording Progress")
        self.window.geometry("300x200")
        self.window.configure(bg="#1e1e1e")
        self.window.resizable(False, False)

        # Make window stay on top
        self.window.attributes("-topmost", True)

        self._setup_ui()
        self._update_progress()

    def _setup_ui(self):
        """Set up the progress window UI."""
        style = {"bg": "#1e1e1e", "fg": "#ffffff"}

        # Status label
        self.status_label = tk.Label(
            self.window, text="🔴 RECORDING", font=("Arial", 12), **style
        )
        self.status_label.pack(pady=10)

        # Time label
        self.time_label = tk.Label(
            self.window, text="00:00:00", font=("Arial", 20, "bold"), **style
        )
        self.time_label.pack(pady=10)

        # Progress info
        self.info_label = tk.Label(self.window, text="", font=("Arial", 10), **style)
        self.info_label.pack(pady=5)

        # Control buttons
        button_frame = tk.Frame(self.window, bg="#1e1e1e")
        button_frame.pack(pady=10)

        btn_style = {"bg": "#3a3a3a", "fg": "white", "activebackground": "#5a5a5a"}

        self.pause_btn = tk.Button(button_frame, text="⏸️ Pause", **btn_style)
        self.pause_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = tk.Button(button_frame, text="⏹️ Stop", **btn_style)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self.cancel_btn = tk.Button(button_frame, text="❌ Cancel", **btn_style)
        self.cancel_btn.pack(side=tk.LEFT, padx=5)

    def _update_progress(self):
        """Update progress display."""
        if self.timer.is_running:
            elapsed = self.timer.get_formatted_elapsed()
            self.time_label.config(text=elapsed)

            # Schedule next update
            self.window.after(1000, self._update_progress)

    def update_info(self, text: str):
        """Update the info label."""
        self.info_label.config(text=text)

    def set_paused(self, paused: bool):
        """Update UI for pause state."""
        if paused:
            self.status_label.config(text="⏸️ PAUSED")
            self.pause_btn.config(text="▶️ Resume")
        else:
            self.status_label.config(text="🔴 RECORDING")
            self.pause_btn.config(text="⏸️ Pause")

    def close(self):
        """Close the progress window."""
        self.window.destroy()


class ProcessingProgressWindow:
    """Separate window showing video processing progress."""

    def __init__(self, parent):
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title("Processing Video...")
        self.window.geometry("400x150")
        self.window.configure(bg="#1e1e1e")
        self.window.resizable(False, False)

        # Make window stay on top and modal
        self.window.attributes("-topmost", True)
        self.window.transient(parent)
        self.window.grab_set()

        # Center the window
        self.window.update_idletasks()
        x = (parent.winfo_x() + parent.winfo_width() // 2) - (
            self.window.winfo_width() // 2
        )
        y = (parent.winfo_y() + parent.winfo_height() // 2) - (
            self.window.winfo_height() // 2
        )
        self.window.geometry(f"+{x}+{y}")

        self._setup_ui()

    def _setup_ui(self):
        """Set up the processing progress window UI."""
        style = {"bg": "#1e1e1e", "fg": "#ffffff"}

        # Status label
        self.status_label = tk.Label(
            self.window, text="🎬 Processing Video...", font=("Arial", 12), **style
        )
        self.status_label.pack(pady=10)

        # Progress bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            self.window,
            variable=self.progress_var,
            maximum=100,
            length=350,
            mode="determinate",
        )
        self.progress_bar.pack(pady=10)

        # Progress percentage label
        self.percentage_label = tk.Label(
            self.window, text="0%", font=("Arial", 10), **style
        )
        self.percentage_label.pack(pady=5)

        # Progress info
        self.info_label = tk.Label(
            self.window, text="Merging audio and video...", font=("Arial", 9), **style
        )
        self.info_label.pack(pady=5)

    def update_progress(self, percentage: float, info: str = ""):
        """Update progress bar and info."""
        self.progress_var.set(percentage)
        self.percentage_label.config(text=f"{percentage:.1f}%")
        if info:
            self.info_label.config(text=info)
        self.window.update_idletasks()

    def close(self):
        """Close the processing window."""
        self.window.destroy()


class SettingsWindow:
    """Settings configuration window."""

    def __init__(self, parent, config_manager: ConfigManager):
        self.parent = parent
        self.config_manager = config_manager
        self.config = config_manager.config

        self.window = tk.Toplevel(parent)
        self.window.title("Settings")
        self.window.geometry("500x600")
        self.window.configure(bg="#1e1e1e")
        self.window.resizable(False, False)

        # Make modal
        self.window.transient(parent)
        self.window.grab_set()

        self._setup_ui()
        self._load_current_settings()

    def _setup_ui(self):
        """Set up the settings window UI."""
        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Video settings tab
        self.video_frame = tk.Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(self.video_frame, text="Video")
        self._setup_video_tab()

        # Audio settings tab
        self.audio_frame = tk.Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(self.audio_frame, text="Audio")
        self._setup_audio_tab()

        # General settings tab
        self.general_frame = tk.Frame(self.notebook, bg="#1e1e1e")
        self.notebook.add(self.general_frame, text="General")
        self._setup_general_tab()

        # Buttons
        self._setup_buttons()

    def _setup_video_tab(self):
        """Set up video settings tab."""
        style = {"bg": "#1e1e1e", "fg": "#ffffff"}
        entry_style = {"bg": "#2e2e2e", "fg": "#ffffff", "insertbackground": "white"}

        row = 0

        # Codec selection
        tk.Label(self.video_frame, text="Video Codec:", **style).grid(
            row=row, column=0, sticky="w", padx=5, pady=5
        )
        self.codec_var = tk.StringVar()
        codec_combo = ttk.Combobox(
            self.video_frame, textvariable=self.codec_var, state="readonly"
        )
        codec_combo["values"] = list(self.config_manager.get_video_codecs().keys())
        codec_combo.grid(row=row, column=1, sticky="w", padx=5, pady=5)
        row += 1

        # Quality setting
        tk.Label(self.video_frame, text="Quality (1-100):", **style).grid(
            row=row, column=0, sticky="w", padx=5, pady=5
        )
        self.quality_var = tk.StringVar()
        tk.Entry(self.video_frame, textvariable=self.quality_var, **entry_style).grid(
            row=row, column=1, sticky="w", padx=5, pady=5
        )
        row += 1

        # Output format
        tk.Label(self.video_frame, text="Output Format:", **style).grid(
            row=row, column=0, sticky="w", padx=5, pady=5
        )
        self.format_var = tk.StringVar()
        format_combo = ttk.Combobox(
            self.video_frame, textvariable=self.format_var, state="readonly"
        )
        format_combo["values"] = self.config_manager.get_output_formats()
        format_combo.grid(row=row, column=1, sticky="w", padx=5, pady=5)

    def _setup_audio_tab(self):
        """Set up audio settings tab."""
        style = {"bg": "#1e1e1e", "fg": "#ffffff"}
        entry_style = {"bg": "#2e2e2e", "fg": "#ffffff", "insertbackground": "white"}

        row = 0

        # Sample rate
        tk.Label(self.audio_frame, text="Sample Rate:", **style).grid(
            row=row, column=0, sticky="w", padx=5, pady=5
        )
        self.sample_rate_var = tk.StringVar()
        rate_combo = ttk.Combobox(
            self.audio_frame, textvariable=self.sample_rate_var, state="readonly"
        )
        rate_combo["values"] = ["22050", "44100", "48000", "96000"]
        rate_combo.grid(row=row, column=1, sticky="w", padx=5, pady=5)
        row += 1

        # Channels
        tk.Label(self.audio_frame, text="Channels:", **style).grid(
            row=row, column=0, sticky="w", padx=5, pady=5
        )
        self.channels_var = tk.StringVar()
        channels_combo = ttk.Combobox(
            self.audio_frame, textvariable=self.channels_var, state="readonly"
        )
        channels_combo["values"] = ["1 (Mono)", "2 (Stereo)"]
        channels_combo.grid(row=row, column=1, sticky="w", padx=5, pady=5)
        row += 1

        # Bitrate
        tk.Label(self.audio_frame, text="Bitrate:", **style).grid(
            row=row, column=0, sticky="w", padx=5, pady=5
        )
        self.bitrate_var = tk.StringVar()
        bitrate_combo = ttk.Combobox(
            self.audio_frame, textvariable=self.bitrate_var, state="readonly"
        )
        bitrate_combo["values"] = ["64k", "128k", "192k", "256k", "320k"]
        bitrate_combo.grid(row=row, column=1, sticky="w", padx=5, pady=5)
        row += 1

        # Audio delay compensation
        tk.Label(self.audio_frame, text="Audio Delay (ms):", **style).grid(
            row=row, column=0, sticky="w", padx=5, pady=5
        )
        self.audio_delay_var = tk.StringVar()
        delay_frame = tk.Frame(self.audio_frame, bg="#1e1e1e")
        delay_frame.grid(row=row, column=1, sticky="w", padx=5, pady=5)

        tk.Entry(
            delay_frame, textvariable=self.audio_delay_var, width=10, **entry_style
        ).pack(side=tk.LEFT)
        tk.Label(
            delay_frame,
            text="(+delay audio, -advance audio)",
            font=("Arial", 8),
            **style,
        ).pack(side=tk.LEFT, padx=(5, 0))

    def _setup_general_tab(self):
        """Set up general settings tab."""
        style = {"bg": "#1e1e1e", "fg": "#ffffff"}
        entry_style = {"bg": "#2e2e2e", "fg": "#ffffff", "insertbackground": "white"}

        row = 0

        # Output directory
        tk.Label(self.general_frame, text="Output Directory:", **style).grid(
            row=row, column=0, sticky="w", padx=5, pady=5
        )
        self.output_dir_var = tk.StringVar()
        dir_frame = tk.Frame(self.general_frame, bg="#1e1e1e")
        dir_frame.grid(row=row, column=1, sticky="w", padx=5, pady=5)

        tk.Entry(
            dir_frame, textvariable=self.output_dir_var, width=30, **entry_style
        ).pack(side=tk.LEFT)
        tk.Button(
            dir_frame,
            text="Browse",
            command=self._browse_directory,
            bg="#3a3a3a",
            fg="white",
        ).pack(side=tk.LEFT, padx=(5, 0))
        row += 1

        # Debug mode
        self.debug_var = tk.BooleanVar()
        tk.Checkbutton(
            self.general_frame, text="Debug Mode", variable=self.debug_var, **style
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=5, pady=5)
        row += 1

        # Auto increment filename
        self.auto_increment_var = tk.BooleanVar()
        tk.Checkbutton(
            self.general_frame,
            text="Auto-increment filenames",
            variable=self.auto_increment_var,
            **style,
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=5, pady=5)

    def _setup_buttons(self):
        """Set up OK/Cancel buttons."""
        button_frame = tk.Frame(self.window, bg="#1e1e1e")
        button_frame.pack(pady=10)

        btn_style = {"bg": "#3a3a3a", "fg": "white", "activebackground": "#5a5a5a"}

        tk.Button(
            button_frame, text="OK", command=self._save_settings, **btn_style
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Cancel", command=self._cancel, **btn_style).pack(
            side=tk.LEFT, padx=5
        )
        tk.Button(
            button_frame,
            text="Reset to Defaults",
            command=self._reset_defaults,
            **btn_style,
        ).pack(side=tk.LEFT, padx=5)

    def _load_current_settings(self):
        """Load current settings into UI."""
        self.codec_var.set(self.config.video_codec)
        self.quality_var.set(str(self.config.video_quality))
        self.format_var.set(self.config.output_format)
        self.sample_rate_var.set(str(self.config.audio_sample_rate))
        self.channels_var.set(
            f"{self.config.audio_channels} ({'Mono' if self.config.audio_channels == 1 else 'Stereo'})"
        )
        self.bitrate_var.set(self.config.audio_bitrate)
        self.audio_delay_var.set(str(self.config.audio_delay_ms))
        self.output_dir_var.set(self.config.output_directory)
        self.debug_var.set(self.config.debug_mode)
        self.auto_increment_var.set(self.config.auto_increment_filename)

    def _browse_directory(self):
        """Browse for output directory."""
        directory = filedialog.askdirectory(initialdir=self.output_dir_var.get())
        if directory:
            self.output_dir_var.set(directory)

    def _save_settings(self):
        """Save settings and close window."""
        try:
            # Update config
            self.config.video_codec = self.codec_var.get()
            self.config.video_quality = int(self.quality_var.get())
            self.config.output_format = self.format_var.get()
            self.config.audio_sample_rate = int(self.sample_rate_var.get())
            self.config.audio_channels = int(self.channels_var.get().split()[0])
            self.config.audio_bitrate = self.bitrate_var.get()
            self.config.audio_delay_ms = int(self.audio_delay_var.get())
            self.config.output_directory = self.output_dir_var.get()
            self.config.debug_mode = self.debug_var.get()
            self.config.auto_increment_filename = self.auto_increment_var.get()

            # Validate and save
            issues = self.config_manager.validate_config()
            if issues:
                messagebox.showerror("Invalid Settings", "\n".join(issues))
                return

            if self.config_manager.save_config():
                messagebox.showinfo("Settings", "Settings saved successfully!")
                self.window.destroy()
            else:
                messagebox.showerror("Error", "Failed to save settings.")

        except ValueError as e:
            messagebox.showerror(
                "Invalid Input", f"Please check your input values: {e}"
            )

    def _cancel(self):
        """Cancel settings changes."""
        self.window.destroy()

    def _reset_defaults(self):
        """Reset settings to defaults."""
        if messagebox.askyesno("Reset Settings", "Reset all settings to defaults?"):
            self.config_manager.reset_to_defaults()
            self._load_current_settings()


class RecorderApp:
    """Main application window with enhanced UI and functionality."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Advanced Screen + Audio Recorder")
        self.root.configure(bg="#1e1e1e")

        # Initialize components
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load_config()

        # Set up logging
        self.logger = setup_logging(
            self.config.log_level,
            self.config.debug_mode,
            "recorder.log" if self.config.debug_mode else None,
        )

        # Check dependencies
        self._check_dependencies()

        # Initialize recording components
        self.recorder: ScreenRecorder | None = None
        self.audio_capture: SystemAudioCapture | None = None
        self.audio_proc: subprocess.Popen | None = None
        self.video_file: str | None = None
        self.audio_file: str | None = None
        self.final_file: str | None = None
        self.last_saved_recording: str | None = None
        self.recording_timer = RecordingTimer()

        # UI components
        self.progress_window: ProgressWindow | None = None
        self.processing_window: ProcessingProgressWindow | None = None
        self.app_checkboxes: dict[str, tk.BooleanVar] = {}
        self.log_text = tk.StringVar(value="Ready")

        # Available screens
        try:
            temp_recorder = ScreenRecorder(1)
            self.screens = temp_recorder.detect_screens()
        except VideoCaptureError as e:
            self.logger.error(f"Failed to detect screens: {e}")
            self.screens = []

        self.selected_screen_index = tk.IntVar(value=self.config.default_monitor)

        self._build_ui()
        self._setup_keyboard_shortcuts()

        # Size the window to its contents (a fixed size cut off the buttons)
        self._fit_window(restore_position=self.config.remember_window_position)

    def _fit_window(self, restore_position: bool = False):
        """Resize the window so every widget is fully visible."""
        self.root.update_idletasks()
        width = self.root.winfo_reqwidth()
        height = self.root.winfo_reqheight()
        self.root.minsize(width, height)
        if restore_position:
            self.root.geometry(
                f"{width}x{height}+{self.config.window_x}+{self.config.window_y}"
            )
        else:
            self.root.geometry(f"{width}x{height}")

    def _check_dependencies(self):
        """Check system dependencies."""
        deps = check_dependencies()
        missing = [dep for dep, available in deps.items() if not available]

        if missing:
            self.logger.warning(f"Missing dependencies: {', '.join(missing)}")
            messagebox.showwarning(
                "Missing Dependencies",
                f"Some features may not work properly.\nMissing: {', '.join(missing)}\n\n"
                "Please install the missing dependencies.",
            )

    def _build_ui(self):
        """Build the main UI."""
        # Configure grid weights for resizing
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        # Main frame
        main_frame = tk.Frame(self.root, bg="#1e1e1e")
        main_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        # Styles
        label_style = {"bg": "#1e1e1e", "fg": "#ffffff"}
        entry_style = {"bg": "#2e2e2e", "fg": "#ffffff", "insertbackground": "white"}
        btn_style = {"bg": "#3a3a3a", "fg": "white", "activebackground": "#5a5a5a"}

        row = 0

        # FPS setting
        tk.Label(main_frame, text="FPS:", **label_style).grid(
            row=row, column=0, sticky="w", pady=5
        )
        self.fps_entry = tk.Entry(main_frame, width=10, **entry_style)
        self.fps_entry.insert(0, str(self.config.fps))
        self.fps_entry.grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        # Screen selector
        if self.screens:
            tk.Label(main_frame, text="Screen:", **label_style).grid(
                row=row, column=0, sticky="w", pady=5
            )
            screen_options = [
                f"{s['name']}: {s['width']}x{s['height']}" for s in self.screens
            ]
            self.screen_combo = ttk.Combobox(
                main_frame, values=screen_options, state="readonly", width=30
            )
            if self.config.default_monitor <= len(self.screens):
                self.screen_combo.current(self.config.default_monitor - 1)
            else:
                self.screen_combo.current(0)
            self.screen_combo.bind("<<ComboboxSelected>>", self._on_screen_selected)
            self.screen_combo.grid(row=row, column=1, columnspan=2, sticky="w", pady=5)
            row += 1

        # Audio apps section with hide/show button
        audio_header_frame = tk.Frame(main_frame, bg="#1e1e1e")
        audio_header_frame.grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(15, 5)
        )

        tk.Label(audio_header_frame, text="Audio Sources:", **label_style).pack(
            side=tk.LEFT
        )

        # Hide/Show sources button
        self.sources_visible = tk.BooleanVar(value=False)
        self.toggle_sources_btn = tk.Button(
            audio_header_frame,
            text="Show Sources",
            command=self._toggle_sources_visibility,
            **btn_style,
            font=("Arial", 8),
        )
        self.toggle_sources_btn.pack(side=tk.LEFT, padx=(10, 0))
        row += 1

        # Audio apps checkboxes, in a scrollable box
        self.sources_box = tk.Frame(main_frame, bg="#1e1e1e")
        self.sources_box.grid(row=row, column=0, columnspan=3, sticky="ew", pady=5)
        self.sources_canvas = tk.Canvas(
            self.sources_box, bg="#1e1e1e", highlightthickness=0, bd=0
        )
        sources_scrollbar = ttk.Scrollbar(
            self.sources_box, orient=tk.VERTICAL, command=self.sources_canvas.yview
        )
        self.sources_canvas.configure(yscrollcommand=sources_scrollbar.set)
        self.sources_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sources_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.apps_frame = tk.Frame(self.sources_canvas, bg="#1e1e1e")
        self.sources_canvas.create_window((0, 0), window=self.apps_frame, anchor="nw")
        self.apps_frame.bind(
            "<Configure>",
            lambda e: self.sources_canvas.configure(
                scrollregion=self.sources_canvas.bbox("all")
            ),
        )

        available_apps = self.config_manager.get_available_apps()
        for idx, app in enumerate(available_apps):
            var = tk.BooleanVar(value=app in self.config.selected_apps)
            chk = tk.Checkbutton(
                self.apps_frame,
                text=app,
                variable=var,
                bg="#1e1e1e",
                fg="#cccccc",
                selectcolor="#2e2e2e",
                activebackground="#333333",
            )
            chk.grid(row=idx // 3, column=idx % 3, sticky="w", padx=5)
            self.app_checkboxes[app] = var

        # Show about 5 rows at a time; scroll for the rest
        self.apps_frame.update_idletasks()
        row_height = max(chk.winfo_reqheight(), 1) if available_apps else 24
        self.sources_canvas.configure(
            width=self.apps_frame.winfo_reqwidth(),
            height=min(self.apps_frame.winfo_reqheight(), row_height * 5),
        )
        self._bind_mousewheel(self.sources_box)

        # Hide sources by default
        self.sources_box.grid_remove()
        row += 1

        # Status and progress
        self.status_label = tk.Label(
            main_frame, textvariable=self.log_text, **label_style
        )
        self.status_label.grid(row=row, column=0, columnspan=3, sticky="w", pady=15)
        row += 1

        # Control buttons: recording controls on one row, the rest below
        buttons_frame = tk.Frame(main_frame, bg="#1e1e1e")
        buttons_frame.grid(row=row, column=0, columnspan=3, pady=(10, 5))
        row += 1
        extra_buttons_frame = tk.Frame(main_frame, bg="#1e1e1e")
        extra_buttons_frame.grid(row=row, column=0, columnspan=3, pady=(0, 10))

        self.start_btn = tk.Button(
            buttons_frame,
            text="🎬 Start Recording",
            command=self.start_recording,
            **btn_style,
        )
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.pause_btn = tk.Button(
            buttons_frame,
            text="⏸️ Pause",
            command=self.pause_recording,
            **btn_style,
            state=tk.DISABLED,
        )
        self.pause_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = tk.Button(
            buttons_frame,
            text="⏹️ Stop Recording",
            command=self.stop_recording,
            **btn_style,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self.cancel_btn = tk.Button(
            buttons_frame,
            text="❌ Cancel",
            command=self.cancel_recording,
            **btn_style,
            state=tk.DISABLED,
        )
        self.cancel_btn.pack(side=tk.LEFT, padx=5)

        self.settings_btn = tk.Button(
            extra_buttons_frame,
            text="⚙️ Settings",
            command=self.open_settings,
            **btn_style,
        )
        self.settings_btn.pack(side=tk.LEFT, padx=5)

        self.open_file_btn = tk.Button(
            extra_buttons_frame,
            text="📂 Open Recording",
            command=self.open_last_recording,
            **btn_style,
            state=tk.DISABLED,
        )
        self.open_file_btn.pack(side=tk.LEFT, padx=5)

        row += 1

        # Progress bar (initially hidden)
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            main_frame, variable=self.progress_var, mode="indeterminate"
        )

        # Keyboard shortcuts help
        help_text = "Shortcuts: F1=Start/Stop, F2=Pause, F3=Settings, F4=Open Recording, F5=Cancel, Esc=Stop"
        buttons_frame.update_idletasks()
        tk.Label(
            main_frame,
            text=help_text,
            **label_style,
            font=("Arial", 8),
            justify=tk.LEFT,
            wraplength=max(buttons_frame.winfo_reqwidth(), 300),
        ).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(20, 0)
        )
        row += 1

        # Footer: version and ways to reach the developer
        footer = tk.Frame(main_frame, bg="#1e1e1e")
        footer.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))
        footer_font = ("Arial", 8)
        tk.Label(
            footer, text=f"screenrec-lite {app_version()}", **label_style,
            font=footer_font,
        ).pack(side=tk.LEFT)
        for text, url in (
            ("Report a problem", issue_url()),
            ("agustinenriquez.dev", WEBSITE_URL),
        ):
            tk.Label(footer, text="·", **label_style, font=footer_font).pack(
                side=tk.LEFT, padx=4
            )
            link = tk.Label(
                footer, text=text, bg="#1e1e1e", fg="#6cb6ff",
                cursor="hand2", font=footer_font + ("underline",),
            )
            link.pack(side=tk.LEFT)
            link.bind("<Button-1>", lambda e, u=url: open_url(u))

    def _setup_keyboard_shortcuts(self):
        """Set up keyboard shortcuts."""
        self.root.bind("<F1>", lambda e: self.toggle_recording())
        self.root.bind("<F2>", lambda e: self.pause_recording())
        self.root.bind("<F3>", lambda e: self.open_settings())
        self.root.bind("<F4>", lambda e: self.open_last_recording())
        self.root.bind("<F5>", lambda e: self.cancel_recording())
        self.root.bind("<Escape>", lambda e: self.stop_recording())

        # Make sure window can receive focus
        self.root.focus_set()

    def _on_screen_selected(self, event=None):
        """Handle screen selection."""
        if hasattr(self, "screen_combo"):
            self.selected_screen_index.set(self.screen_combo.current() + 1)

    def _toggle_sources_visibility(self):
        """Toggle the visibility of audio sources checkboxes."""
        if self.sources_visible.get():
            # Hide sources
            self.sources_box.grid_remove()
            self.sources_visible.set(False)
            self.toggle_sources_btn.config(text="Show Sources")
        else:
            # Show sources
            self.sources_box.grid()
            self.sources_visible.set(True)
            self.toggle_sources_btn.config(text="Hide Sources")
        # Grow or shrink the window to fit, keeping it where it is
        self.root.update_idletasks()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self._fit_window()
        self.root.geometry(f"+{x}+{y}")

    def _bind_mousewheel(self, widget):
        """Scroll the sources list with the mouse wheel while hovering it."""

        def on_wheel(event):
            if event.num == 4 or getattr(event, "delta", 0) > 0:
                self.sources_canvas.yview_scroll(-1, "units")
            elif event.num == 5 or getattr(event, "delta", 0) < 0:
                self.sources_canvas.yview_scroll(1, "units")
            return "break"

        def enable(_event):
            self.root.bind_all("<MouseWheel>", on_wheel)
            self.root.bind_all("<Button-4>", on_wheel)
            self.root.bind_all("<Button-5>", on_wheel)

        def disable(_event):
            self.root.unbind_all("<MouseWheel>")
            self.root.unbind_all("<Button-4>")
            self.root.unbind_all("<Button-5>")

        widget.bind("<Enter>", enable)
        widget.bind("<Leave>", disable)

    def _log(self, msg: str):
        """Update log display."""
        self.logger.info(msg)
        self.log_text.set(msg)

        # Update progress window if open
        if self.progress_window:
            self.progress_window.update_info(msg)

    def toggle_recording(self):
        """Toggle recording state."""
        if not self.recorder or not self.recorder.recording:
            self.start_recording()
        else:
            self.stop_recording()

    def pause_recording(self):
        """Pause/resume recording."""
        if self.recorder and self.recorder.recording:
            self.recorder.pause_recording()

            # Pause/resume audio recording as well
            if self.audio_capture:
                try:
                    self.audio_capture.pause_recording()
                except AudioCaptureError as e:
                    self.logger.warning(f"Audio pause/resume failed: {e}")

            # Update UI
            if self.recorder.paused:
                self.pause_btn.config(text="▶️ Resume")
                self._log("Recording paused")
            else:
                self.pause_btn.config(text="⏸️ Pause")
                self._log("Recording resumed")

            if self.progress_window:
                self.progress_window.set_paused(self.recorder.paused)

    def start_recording(self):
        """Start recording with enhanced error handling."""
        try:
            self._log("=== START RECORDING CALLED ===")

            # Check if already recording
            if self.recorder and self.recorder.recording:
                self._log("Already recording - ignoring start request")
                return

            self.audio_start_time = None

            # Validate FPS
            valid, fps = validate_fps(self.fps_entry.get())
            if not valid:
                messagebox.showerror(
                    "Invalid FPS", "Please enter a valid FPS value (1-120)."
                )
                return

            # Get selected apps
            selected_apps = [
                app for app, var in self.app_checkboxes.items() if var.get()
            ]
            if not selected_apps:
                if not messagebox.askyesno(
                    "No Audio Sources",
                    "No audio sources selected. Continue with video only?",
                ):
                    return

            # In the snap, audio capture needs a permission the user may not
            # have granted yet; without it recordings would be silent.
            if selected_apps and snap_audio_record_missing():
                if self._ask_audio_permission() != "video":
                    return
                selected_apps = []

            # Initialize recorder
            self.recorder = ScreenRecorder(
                fps,
                monitor_index=self.selected_screen_index.get(),
                codec=self.config.video_codec,
                quality=self.config.video_quality,
                log_callback=self._log,
            )

            # Initialize audio capture if apps selected
            if selected_apps:
                self.audio_capture = SystemAudioCapture(
                    selected_apps,
                    sample_rate=self.config.audio_sample_rate,
                    channels=self.config.audio_channels,
                    log_callback=self._log,
                )

                if not self.audio_capture.setup():
                    messagebox.showerror(
                        "Audio Setup Failed",
                        "Failed to set up audio capture. Continue with video only?",
                    )
                    if not messagebox.askyesno(
                        "Continue?", "Record video without audio?"
                    ):
                        return
                    self.audio_capture = None

            # Generate output filename
            if self.config.auto_increment_filename:
                base_name = Path(self.config.output_directory) / "recording"
                temp_video = f"{base_name}_temp.avi"
                # Generate unique audio filename for each recording
                import time

                self.audio_file = f"temp_audio_{int(time.time())}.wav"
            else:
                temp_video = filedialog.asksaveasfilename(
                    defaultextension=f".{self.config.output_format}",
                    filetypes=[
                        (
                            f"{self.config.output_format.upper()} files",
                            f"*.{self.config.output_format}",
                        )
                    ],
                )
                if not temp_video:
                    return
                temp_video = temp_video.replace(
                    f".{self.config.output_format}", "_temp.avi"
                )
                # Generate unique audio filename for each recording
                import time

                self.audio_file = f"temp_audio_{int(time.time())}.wav"

            # Open the screen capture before starting audio, so the time the
            # user spends in the "Share screen" dialog doesn't put audio ahead.
            self.start_btn.config(state=tk.DISABLED)
            self._log("Preparing screen capture...")
            self._capture_result = None

            def _open_capture():
                try:
                    self.recorder.open_capture()
                    self._capture_result = "ok"
                except Exception as e:
                    self._capture_result = e

            threading.Thread(target=_open_capture, daemon=True).start()
            self._wait_for_capture(temp_video)

        except (VideoCaptureError, AudioCaptureError) as e:
            messagebox.showerror("Recording Error", str(e))
            self._cleanup_recording()
        except Exception as e:
            self.logger.error(f"Unexpected error starting recording: {e}")
            messagebox.showerror("Error", f"Failed to start recording: {e}")
            self._cleanup_recording()

    def _ask_audio_permission(self) -> str:
        """Explain how to allow audio recording. Returns "video" or "cancel"."""
        bg, fg = "#1e1e1e", "#ffffff"
        btn = {"bg": "#3a3a3a", "fg": "white", "activebackground": "#5a5a5a"}
        dialog = tk.Toplevel(self.root, bg=bg, padx=16, pady=14)
        dialog.title("Audio recording is turned off")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        choice = {"value": "cancel"}

        tk.Label(
            dialog,
            text="Audio recording is turned off for this app",
            bg=bg,
            fg=fg,
            font=("Arial", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            dialog,
            text=(
                "To record sound, run this once in a terminal,\n"
                "then restart the app:"
            ),
            bg=bg,
            fg="#cccccc",
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(6, 6))

        cmd_var = tk.StringVar(value=SNAP_CONNECT_AUDIO_CMD)
        entry = tk.Entry(
            dialog,
            textvariable=cmd_var,
            state="readonly",
            readonlybackground="#2e2e2e",
            fg=fg,
            width=len(SNAP_CONNECT_AUDIO_CMD) + 2,
            relief=tk.FLAT,
        )
        entry.pack(fill=tk.X, ipady=4)

        buttons = tk.Frame(dialog, bg=bg)
        buttons.pack(fill=tk.X, pady=(12, 0))

        def copy_command():
            self.root.clipboard_clear()
            self.root.clipboard_append(SNAP_CONNECT_AUDIO_CMD)
            copy_btn.config(text="Copied")

        def finish(value: str):
            choice["value"] = value
            dialog.destroy()

        copy_btn = tk.Button(buttons, text="Copy command", command=copy_command, **btn)
        copy_btn.pack(side=tk.LEFT)
        tk.Button(
            buttons, text="Cancel", command=lambda: finish("cancel"), **btn
        ).pack(side=tk.RIGHT)
        tk.Button(
            buttons,
            text="Record video only",
            command=lambda: finish("video"),
            **btn,
        ).pack(side=tk.RIGHT, padx=(0, 6))

        dialog.protocol("WM_DELETE_WINDOW", lambda: finish("cancel"))
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + 40
        y = self.root.winfo_rooty() + 40
        dialog.geometry(f"+{x}+{y}")
        dialog.grab_set()
        self.root.wait_window(dialog)
        return choice["value"]

    def _wait_for_capture(self, temp_video: str):
        """Poll until the capture is open, then start recording."""
        result = self._capture_result
        if result is None:
            self.root.after(100, self._wait_for_capture, temp_video)
            return
        if result != "ok":
            self.start_btn.config(state=tk.NORMAL)
            self._log(f"Could not start screen capture: {result}")
            messagebox.showerror("Screen Capture", str(result))
            self._cleanup_recording()
            return
        try:
            self._begin_recording(temp_video)
        except (VideoCaptureError, AudioCaptureError) as e:
            messagebox.showerror("Recording Error", str(e))
            self._cleanup_recording()
        except Exception as e:
            self.logger.error(f"Unexpected error starting recording: {e}")
            messagebox.showerror("Error", f"Failed to start recording: {e}")
            self._cleanup_recording()

    def _begin_recording(self, temp_video: str):
        """Start audio and video once the screen capture is ready."""
        # Start audio recording first (it takes longer to initialize)
        if self.audio_capture:
            self.audio_proc = self.audio_capture.start_recording(self.audio_file)
            # Small delay to let audio recording stabilize
            import time

            time.sleep(0.1)

        # Start video recording
        self.video_file = self.recorder.start_recording(temp_video)

        # Update UI
        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL, text="⏸️ Pause")
        self.stop_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.NORMAL)
        self.progress_bar.grid(row=100, column=0, columnspan=3, sticky="ew", pady=5)
        self.progress_bar.start()

        # Start timer and show progress window
        self.recording_timer.start()
        self.progress_window = ProgressWindow(self.root, self.recording_timer)
        self.progress_window.pause_btn.config(command=self.pause_recording)
        self.progress_window.stop_btn.config(command=self.stop_recording)
        self.progress_window.cancel_btn.config(command=self.cancel_recording)

        self._log("Recording started...")

    def stop_recording(self):
        """Stop recording with proper cleanup."""
        if not self.recorder or not self.recorder.recording:
            return

        self._log("Stopping recording...")

        # Update UI immediately to show responsiveness
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.DISABLED, text="Stopping...")
        self.cancel_btn.config(state=tk.DISABLED)
        self.root.update_idletasks()

        def _stop_recording_thread():
            """Handle the actual stopping in a separate thread to keep UI responsive."""
            try:
                self._log("=== UI STOP RECORDING THREAD STARTED ===")
                # Stop timer first
                self.recording_timer.stop()
                self._log("Recording timer stopped")

                # Stop video recording first and wait for it to complete
                self._log("Stopping video recording...")
                video_output = None
                if self.recorder:
                    video_output = self.recorder.stop_recording()
                    self._log(f"Video recording stop returned: {video_output}")
                else:
                    self._log("No recorder object found")

                # Give the video writer a moment to finalize the file
                import time

                self._log("Waiting 1 second for video file finalization...")
                time.sleep(1.0)

                # Stop audio recording
                self._log("Stopping audio recording...")
                if self.audio_capture:
                    self.audio_capture.stop_recording()
                    time.sleep(0.5)  # Allow audio to finish writing
                    self.audio_start_time = self.audio_capture.start_time
                    self._log("Audio stopped, now cleaning up...")
                    self.audio_capture.cleanup()
                    self.audio_capture = None
                    self._log("Audio recording stopped and cleaned up")
                else:
                    self._log("No audio capture to stop")

                if self.audio_proc:
                    self.audio_proc = None
                    self._log("Audio process reference cleared")

                # Verify files exist before processing
                self._log("=== FILE VERIFICATION ===")
                if video_output and Path(video_output).exists():
                    video_size = Path(video_output).stat().st_size
                    self._log(
                        f"✓ Video file exists: {video_output} ({video_size} bytes)"
                    )
                else:
                    self._log(f"✗ Video file missing or empty: {video_output}")

                if self.audio_file and Path(self.audio_file).exists():
                    audio_size = Path(self.audio_file).stat().st_size
                    self._log(
                        f"✓ Audio file exists: {self.audio_file} ({audio_size} bytes)"
                    )
                else:
                    self._log(f"✗ Audio file missing or empty: {self.audio_file}")

                # Process output only if we have valid video
                self._log("=== PROCESSING OUTPUT ===")
                if video_output and Path(video_output).exists():
                    self._log("Starting output processing...")
                    final_output = self._process_output(video_output)
                    if final_output:

                        def _update_ui_with_saved_recording():
                            self.last_saved_recording = final_output
                            self.open_file_btn.config(state=tk.NORMAL)
                            self._log(f"✓ Recording saved successfully: {final_output}")

                        self.root.after(0, _update_ui_with_saved_recording)
                    else:

                        def _update_ui_with_error():
                            self._log("✗ Failed to process recording")

                        self.root.after(0, _update_ui_with_error)
                else:

                    def _update_ui_with_no_video():
                        self._log("✗ No video file was created - recording failed")

                    self.root.after(0, _update_ui_with_no_video)

            except Exception as e:
                error_msg = f"Error stopping recording: {e}"
                self.logger.error(error_msg)
                import traceback

                self.logger.error(f"Stop recording traceback: {traceback.format_exc()}")
                self.root.after(
                    0,
                    lambda: messagebox.showerror("Error", error_msg),
                )
            finally:
                # Always schedule UI cleanup on main thread, regardless of success or failure
                self.root.after(0, self._cleanup_recording)

        # Start the stopping process in a separate thread
        import threading

        stop_thread = threading.Thread(target=_stop_recording_thread, daemon=True)
        stop_thread.start()

        # Add a backup cleanup after a reasonable timeout (30 seconds)
        # This ensures buttons are re-enabled even if something goes wrong
        # Only trigger if we're not currently recording
        def _backup_cleanup():
            if self.start_btn.cget("state") == tk.DISABLED and (
                not self.recorder or not self.recorder.recording
            ):
                self.logger.warning("Backup cleanup triggered - re-enabling buttons")
                self._cleanup_recording()

        self.root.after(30000, _backup_cleanup)  # Increased from 10 to 30 seconds

    def cancel_recording(self):
        """Cancel recording without saving."""
        if not self.recorder or not self.recorder.recording:
            return

        self._log("Cancelling recording...")

        # Update UI immediately to show responsiveness
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.DISABLED, text="Cancelling...")
        self.root.update_idletasks()

        def _cancel_recording_thread():
            """Handle the actual cancellation in a separate thread to keep UI responsive."""
            try:
                # Stop recording
                self.recording_timer.stop()
                self.recorder.stop_recording()

                # Stop audio
                if self.audio_capture:
                    self.audio_capture.stop_recording()
                    self.audio_capture.cleanup()
                    self.audio_capture = None

                if self.audio_proc:
                    self.audio_proc = None

                # Clean up temp files without processing/saving
                temp_files = []
                if self.video_file and Path(self.video_file).exists():
                    temp_files.append(self.video_file)
                if self.audio_file and Path(self.audio_file).exists():
                    temp_files.append(self.audio_file)

                if temp_files:
                    from video import cleanup_temp_files

                    cleanup_temp_files(temp_files, self._log)

                def _update_ui_with_cancel():
                    self._log("Recording cancelled - no files saved")

                self.root.after(0, _update_ui_with_cancel)

            except Exception as e:
                error_msg = f"Error cancelling recording: {e}"
                self.logger.error(error_msg)
                self.root.after(
                    0,
                    lambda: messagebox.showerror("Error", error_msg),
                )
            finally:
                # Always schedule UI cleanup on main thread
                self.root.after(0, self._cleanup_recording)

        # Start the cancellation process in a separate thread
        import threading

        cancel_thread = threading.Thread(target=_cancel_recording_thread, daemon=True)
        cancel_thread.start()

        # Add a backup cleanup after a reasonable timeout (30 seconds)
        def _backup_cleanup():
            if self.start_btn.cget("state") == tk.DISABLED and (
                not self.recorder or not self.recorder.recording
            ):
                self.logger.warning(
                    "Backup cleanup triggered after cancel - re-enabling buttons"
                )
                self._cleanup_recording()

        self.root.after(30000, _backup_cleanup)  # Increased from 10 to 30 seconds

    def _audio_lead_seconds(self) -> float:
        """How many seconds the audio started before the first video frame."""
        audio_start = getattr(self, "audio_start_time", None)
        video_start = self.recorder.first_frame_time if self.recorder else None
        if audio_start is None or video_start is None:
            self._log("Couldn't measure audio/video start times; not aligning")
            return 0.0
        lead = video_start - audio_start
        if not -5.0 < lead < 5.0:  # clocks disagree; don't trust it
            self._log(f"Measured audio lead {lead:.3f}s looks wrong; not aligning")
            return 0.0
        return lead

    def _process_output(self, video_file: str) -> str | None:
        """Process the recorded output (merge audio/video, cleanup)."""
        # Show processing progress window
        self.processing_window = ProcessingProgressWindow(self.root)

        def progress_callback(percentage: float, info: str):
            """Update processing progress."""
            if self.processing_window:
                self.processing_window.update_progress(percentage, info)

        try:
            if self.config.auto_increment_filename:
                final_output = get_incremental_filename(
                    base_name=str(Path(self.config.output_directory) / "recording"),
                    extension=f".{self.config.output_format}",
                )
            else:
                final_output = video_file.replace(
                    "_temp.avi", f".{self.config.output_format}"
                )

            # Check file sizes before processing
            video_size = (
                Path(video_file).stat().st_size if Path(video_file).exists() else 0
            )
            audio_size = (
                Path(self.audio_file).stat().st_size
                if self.audio_file and Path(self.audio_file).exists()
                else 0
            )

            self._log(f"Video file: {video_file} ({video_size} bytes)")
            self._log(f"Audio file: {self.audio_file} ({audio_size} bytes)")

            # Merge audio and video if both exist
            if (
                self.audio_file
                and Path(self.audio_file).exists()
                and Path(video_file).exists()
            ):
                if video_size < 1000:  # Less than 1KB indicates a problem
                    self._log("WARNING: Video file is very small, may be corrupted")

                self._log("Merging audio and video...")
                success = merge_audio_video(
                    video_file,
                    self.audio_file,
                    final_output,
                    video_codec="libx264"
                    if self.config.output_format == "mp4"
                    else "copy",
                    audio_codec="aac",
                    audio_delay_ms=self.config.audio_delay_ms,
                    audio_lead_s=self._audio_lead_seconds(),
                    log_callback=self._log,
                    progress_callback=progress_callback,
                )

                if success:
                    # Check final file size
                    final_size = (
                        Path(final_output).stat().st_size
                        if Path(final_output).exists()
                        else 0
                    )
                    self._log(f"Final file: {final_output} ({final_size} bytes)")

                    # Clean up temporary files
                    temp_files = [video_file]
                    if self.audio_file:
                        temp_files.append(self.audio_file)
                    cleanup_temp_files(temp_files, self._log)
                    return final_output
                else:
                    # Keep original video file if merge failed
                    self._log("Merge failed, keeping original video file")
                    import shutil

                    shutil.move(video_file, final_output)
                    return final_output
            else:
                # Only video, rename to final output
                missing_files = []
                if not Path(video_file).exists():
                    missing_files.append("video")
                if not self.audio_file or not Path(self.audio_file).exists():
                    missing_files.append("audio")

                if missing_files:
                    self._log(f"Missing files: {', '.join(missing_files)}")

                if Path(video_file).exists():
                    import shutil

                    shutil.move(video_file, final_output)

                    # Clean up audio temp file if it exists
                    if self.audio_file and Path(self.audio_file).exists():
                        cleanup_temp_files([self.audio_file], self._log)

                    return final_output
                else:
                    self._log("ERROR: No video file to save")

                    # Clean up temp files even if processing failed
                    temp_files = []
                    if self.audio_file and Path(self.audio_file).exists():
                        temp_files.append(self.audio_file)
                    if temp_files:
                        cleanup_temp_files(temp_files, self._log)

                    return None

        except Exception as e:
            self.logger.error(f"Error processing output: {e}")

            # Clean up temp files even if processing failed
            temp_files = []
            if (
                hasattr(self, "audio_file")
                and self.audio_file
                and Path(self.audio_file).exists()
            ):
                temp_files.append(self.audio_file)
            if "video_file" in locals() and Path(video_file).exists():
                temp_files.append(video_file)
            if temp_files:
                cleanup_temp_files(temp_files, self._log)

            return None
        finally:
            # Close processing window
            if self.processing_window:
                self.processing_window.close()
                self.processing_window = None

    def _cleanup_recording(self):
        """Clean up recording state."""
        self.logger.info("=== UI CLEANUP STARTED ===")

        # Only cleanup if we're actually not recording
        if self.recorder and self.recorder.recording:
            self.logger.warning(
                "Cleanup called but recording is still active - skipping cleanup"
            )
            return

        # Release a screen share that was opened but never recorded
        if self.recorder:
            self.recorder.close_capture()

        # Undo audio routing if recording never started (e.g. share cancelled)
        if self.audio_capture and not self.audio_capture.recording_process:
            self.audio_capture.cleanup()
            self.audio_capture = None

        # Update UI
        self.start_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED, text="⏸️ Pause")
        self.stop_btn.config(state=tk.DISABLED, text="⏹️ Stop Recording")
        self.cancel_btn.config(state=tk.DISABLED, text="❌ Cancel")
        self.progress_bar.stop()
        self.progress_bar.grid_remove()

        # Close progress window
        if self.progress_window:
            self.progress_window.close()
            self.progress_window = None
            self.logger.info("Progress window closed")

        # Close processing window if still open
        if self.processing_window:
            self.processing_window.close()
            self.processing_window = None
            self.logger.info("Processing window closed")

        # Reset timer
        self.recording_timer.reset()
        self.logger.info("=== UI CLEANUP COMPLETED ===")

    def open_settings(self):
        """Open settings window."""
        SettingsWindow(self.root, self.config_manager)

    def open_last_recording(self):
        """Open the last saved recording file."""
        if not self.last_saved_recording:
            messagebox.showwarning("No Recording", "No recording file to open.")
            return

        self._log(f"Attempting to open file: {self.last_saved_recording}")
        self._log(f"File exists: {Path(self.last_saved_recording).exists()}")
        self._log(
            f"File size: {Path(self.last_saved_recording).stat().st_size if Path(self.last_saved_recording).exists() else 'N/A'} bytes"
        )

        if not Path(self.last_saved_recording).exists():
            messagebox.showerror(
                "File Not Found",
                f"Recording file not found:\n{self.last_saved_recording}",
            )
            return

        try:
            # Open the file with the default system application
            import os
            import platform

            system = platform.system()
            if system == "Windows":
                os.startfile(self.last_saved_recording)
                self._log(f"Opened recording: {Path(self.last_saved_recording).name}")
            elif system == "Darwin":  # macOS
                result = subprocess.run(
                    ["open", self.last_saved_recording], capture_output=True, text=True
                )
                if result.returncode == 0:
                    self._log(
                        f"Opened recording: {Path(self.last_saved_recording).name}"
                    )
                else:
                    raise Exception(
                        f"Command failed with return code {result.returncode}: {result.stderr}"
                    )
            else:  # Linux and other Unix-like systems
                # Try multiple methods for Linux
                methods = [
                    [
                        "/usr/bin/vlc",
                        "--intf",
                        "qt",
                        self.last_saved_recording,
                    ],  # VLC with Qt interface (full path)
                    [
                        "/usr/bin/vlc",
                        self.last_saved_recording,
                    ],  # Standard VLC (full path)
                    ["vlc", self.last_saved_recording],  # VLC from PATH
                    ["mpv", self.last_saved_recording],  # MPV player
                    [
                        "xdg-open",
                        self.last_saved_recording,
                    ],  # System default (may have Qt issues)
                    ["gnome-open", self.last_saved_recording],
                    ["kde-open", self.last_saved_recording],
                ]

                success = False
                for method in methods:
                    try:
                        self._log(f"Trying to open with: {' '.join(method)}")

                        # Create a clean environment to avoid Qt conflicts
                        clean_env = os.environ.copy()
                        # Remove problematic Qt plugin paths that might interfere
                        qt_vars_to_remove = [
                            "QT_PLUGIN_PATH",
                            "QT_QPA_PLATFORM_PLUGIN_PATH",
                        ]
                        for var in qt_vars_to_remove:
                            clean_env.pop(var, None)

                        # Use Popen to avoid blocking and allow the application to start
                        process = subprocess.Popen(
                            method,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            start_new_session=True,
                            env=clean_env,
                        )
                        # Give it a moment to start
                        import time

                        time.sleep(1.0)

                        # Check if process is still running (didn't immediately fail)
                        return_code = process.poll()
                        if return_code is None:
                            success = True
                            self._log(
                                f"Successfully opened recording with {method[0]}: {Path(self.last_saved_recording).name}"
                            )
                            break
                        else:
                            stdout, stderr = process.communicate()
                            self._log(
                                f"{method[0]} failed with return code {return_code}: {stderr.decode() if stderr else 'No error message'}"
                            )
                    except FileNotFoundError as e:
                        self._log(f"{method[0]} not found: {e}")
                        continue
                    except Exception as e:
                        self._log(f"{method[0]} failed with exception: {e}")
                        continue

                if not success:
                    # Last resort: show file location in file manager
                    try:
                        subprocess.Popen(
                            ["xdg-open", str(Path(self.last_saved_recording).parent)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        self._log(
                            f"Opened folder containing: {Path(self.last_saved_recording).name}"
                        )
                    except Exception:
                        raise Exception(
                            f"Could not open file or folder. Available methods failed. Try manually opening: {self.last_saved_recording}"
                        )

        except Exception as e:
            self.logger.error(f"Failed to open recording: {e}")
            # Show the file path in the error so user can manually open it
            messagebox.showerror(
                "Error",
                f"Failed to open recording automatically.\n\nFile location:\n{self.last_saved_recording}\n\nError: {e}",
            )
            # Copy file path to clipboard if possible
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(self.last_saved_recording)
                messagebox.showinfo(
                    "File Path Copied",
                    f"File path copied to clipboard:\n{self.last_saved_recording}",
                )
            except Exception:
                pass

    def on_closing(self):
        """Handle application closing."""
        # Save window position if enabled
        if self.config.remember_window_position:
            geometry = self.root.geometry()
            # Parse geometry string (WxH+X+Y)
            size_pos = geometry.split("+")
            if len(size_pos) >= 3:
                self.config.window_x = int(size_pos[1])
                self.config.window_y = int(size_pos[2])
                self.config_manager.save_config()

        # Stop recording if in progress
        if self.recorder and self.recorder.recording:
            if messagebox.askyesno(
                "Recording in Progress", "Recording is in progress. Stop and exit?"
            ):
                self.stop_recording()
            else:
                return

        # Clean up
        if self.audio_capture:
            self.audio_capture.cleanup()

        self.root.destroy()


REPO_URL = "https://github.com/cagustinenriquez/minimal-screen-recorder"
WEBSITE_URL = "https://www.agustinenriquez.dev"


def app_version() -> str:
    """Snap version when running as a snap, otherwise "from source"."""
    return os.environ.get("SNAP_VERSION") or "from source"


def issue_url() -> str:
    """New GitHub issue, pre-filled with the details needed to debug."""
    os_name = "unknown"
    for path in ("/var/lib/snapd/hostfs/etc/os-release", "/etc/os-release"):
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        os_name = line.split("=", 1)[1].strip().strip('"')
                        break
            break
        except OSError:
            continue
    session = os.environ.get("XDG_SESSION_TYPE") or (
        "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"
    )
    body = (
        "**What happened?**\n\n\n"
        "**What did you expect?**\n\n\n"
        "---\n"
        f"- screenrec-lite: {app_version()}\n"
        f"- OS: {os_name}\n"
        f"- Session: {session}\n"
    )
    return f"{REPO_URL}/issues/new?" + urllib.parse.urlencode({"body": body})


def open_url(url: str) -> None:
    """Open a link in the user's browser (works inside the snap too)."""
    try:
        subprocess.Popen(
            ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError:
        webbrowser.open(url)


def set_window_icon(root: tk.Tk) -> None:
    """Use the app icon for the window (shown in the dock and Alt+Tab)."""
    here = Path(__file__).resolve().parent
    candidates = [
        Path(os.environ.get("SNAP", "/nonexistent")) / "meta/gui/screenrec-lite.png",
        here / "snap/gui/screenrec-lite.png",  # running from source
    ]
    for path in candidates:
        if path.exists():
            try:
                root._icon = tk.PhotoImage(file=str(path))  # keep a reference
                root.iconphoto(True, root._icon)
            except tk.TclError:
                pass
            return


def main():
    """Main application entry point."""
    root = tk.Tk(className="screenrec-lite")
    set_window_icon(root)
    app = RecorderApp(root)

    # Set up window close handler
    root.protocol("WM_DELETE_WINDOW", app.on_closing)

    # Start the application
    root.mainloop()


if __name__ == "__main__":
    main()
