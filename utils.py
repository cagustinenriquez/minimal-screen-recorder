"""Utility functions and logging setup for the screen recorder."""

import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path


def setup_logging(
    log_level: str = "INFO", debug_mode: bool = False, log_file: str | None = None
) -> logging.Logger:
    """Set up logging configuration."""

    # Create logs directory if needed
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

    # Configure logging format
    if debug_mode:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
    else:
        log_format = "%(asctime)s - %(levelname)s - %(message)s"

    # Set up handlers
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=log_format,
        handlers=handlers,
        force=True,
    )

    logger = logging.getLogger("ScreenRecorder")
    logger.info(f"Logging initialized - Level: {log_level}, Debug: {debug_mode}")

    return logger


def get_incremental_filename(
    base_name: str = "output", extension: str = ".mp4", directory: str = ""
) -> str:
    """Generate an incremental filename that doesn't exist."""

    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        base_path = Path(directory) / base_name
    else:
        base_path = Path(base_name)

    i = 1
    while True:
        filename = f"{base_path}_{i}{extension}"
        if not Path(filename).exists():
            return filename
        i += 1


def format_time(seconds: float) -> str:
    """Format seconds into HH:MM:SS format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_file_size(bytes_size: int) -> str:
    """Format file size in human readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} TB"


def validate_fps(fps: str) -> tuple[bool, float]:
    """Validate FPS input."""
    try:
        fps_val = float(fps)
        if fps_val <= 0 or fps_val > 120:
            return False, 0.0
        return True, fps_val
    except ValueError:
        return False, 0.0


def validate_monitor_index(index: int, max_monitors: int) -> bool:
    """Validate monitor index."""
    return 1 <= index <= max_monitors


def safe_remove_file(filepath: str, logger: logging.Logger | None = None) -> bool:
    """Safely remove a file with error handling."""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            if logger:
                logger.debug(f"Removed file: {filepath}")
            return True
    except Exception as e:
        if logger:
            logger.error(f"Failed to remove file {filepath}: {e}")
        return False
    return True


class RecordingTimer:
    """Timer utility for tracking recording duration (paused time not counted)."""

    def __init__(self):
        self.start_time: float | None = None
        self.is_running = False
        self.paused_at: float | None = None

    def start(self):
        """Start the timer."""
        self.start_time = time.monotonic()
        self.paused_at = None
        self.is_running = True

    def pause(self):
        """Freeze the elapsed time until resume()."""
        if self.start_time is not None and self.paused_at is None:
            self.paused_at = time.monotonic()

    def resume(self):
        """Continue counting; the paused time is left out."""
        if self.paused_at is not None:
            self.start_time += time.monotonic() - self.paused_at
            self.paused_at = None

    def stop(self):
        """Stop the timer."""
        self.is_running = False

    def reset(self):
        """Reset the timer."""
        self.start_time = None
        self.paused_at = None
        self.is_running = False

    def get_elapsed(self) -> float:
        """Get elapsed recording time in seconds."""
        if self.start_time is None:
            return 0.0
        now = self.paused_at if self.paused_at is not None else time.monotonic()
        return now - self.start_time

    def get_formatted_elapsed(self) -> str:
        """Get formatted elapsed time."""
        return format_time(self.get_elapsed())


class CallbackLogger:
    """Logger that can call a callback function for UI updates."""

    def __init__(
        self, logger: logging.Logger, callback: Callable[[str], None] | None = None
    ):
        self.logger = logger
        self.callback = callback

    def debug(self, msg: str):
        self.logger.debug(msg)
        if self.callback:
            self.callback(f"DEBUG: {msg}")

    def info(self, msg: str):
        self.logger.info(msg)
        if self.callback:
            self.callback(msg)

    def warning(self, msg: str):
        self.logger.warning(msg)
        if self.callback:
            self.callback(f"WARNING: {msg}")

    def error(self, msg: str):
        self.logger.error(msg)
        if self.callback:
            self.callback(f"ERROR: {msg}")


def check_dependencies() -> dict[str, bool]:
    """Check if required system dependencies are available."""
    dependencies = {}

    # Check for ffmpeg
    try:
        import subprocess

        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        dependencies["ffmpeg"] = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        dependencies["ffmpeg"] = False

    # Check for pactl (PulseAudio)
    try:
        result = subprocess.run(["pactl", "--version"], capture_output=True, timeout=5)
        dependencies["pulseaudio"] = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        dependencies["pulseaudio"] = False

    # Check Python packages
    try:
        import cv2  # noqa: F401

        dependencies["opencv"] = True
    except ImportError:
        dependencies["opencv"] = False

    try:
        import mss  # noqa: F401

        dependencies["mss"] = True
    except ImportError:
        dependencies["mss"] = False

    try:
        import numpy  # noqa: F401

        dependencies["numpy"] = True
    except ImportError:
        dependencies["numpy"] = False

    return dependencies
