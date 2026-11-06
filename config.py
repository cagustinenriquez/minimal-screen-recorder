"""Configuration management for the screen recorder."""

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class RecorderConfig:
    """Configuration settings for the screen recorder."""

    # Video settings
    fps: float = 20.0
    video_codec: str = "XVID"
    video_quality: int = 95
    output_format: str = "mp4"

    # Audio settings
    audio_sample_rate: int = 48000
    audio_channels: int = 2
    audio_bitrate: str = "128k"
    # Extra audio shift in ms on top of the automatic sync (+ later, - earlier)
    audio_delay_ms: int = 0

    # UI settings
    theme: str = "dark"
    window_width: int = 446
    window_height: int = 343
    remember_window_position: bool = True
    window_x: int = 100
    window_y: int = 100

    # Recording settings
    selected_apps: list[str] = None
    default_monitor: int = 1
    output_directory: str = ""
    auto_increment_filename: bool = True

    # Debug settings
    debug_mode: bool = False
    log_level: str = "INFO"

    def __post_init__(self):
        if self.selected_apps is None:
            self.selected_apps = [
                "Firefox",
                "Chrome",
                "Chromium",
                "zoom",
                "Spotify",
                "discord",
            ]
        if not self.output_directory:
            # Inside a snap, Path.home() is the snap's private dir; use the real home.
            home = Path(os.environ.get("SNAP_REAL_HOME") or Path.home())
            self.output_directory = str(home / "Videos" / "ScreenRecorder")


class ConfigManager:
    """Manages configuration loading, saving, and validation."""

    def __init__(self, config_file: str = "recorder_config.json"):
        self.config_file = Path(config_file)
        self.config = RecorderConfig()
        self.logger = logging.getLogger(__name__)

    def load_config(self) -> RecorderConfig:
        """Load configuration from file."""
        try:
            if self.config_file.exists():
                with open(self.config_file) as f:
                    data = json.load(f)
                    # Update config with loaded data
                    # -250 was the old default, a fixed guess to make up for
                    # audio and video starting at different times. Sync is now
                    # measured, so drop the guess if the user never changed it.
                    if data.get("audio_delay_ms") == -250:
                        data["audio_delay_ms"] = 0
                    for field in fields(RecorderConfig):
                        if field.name in data:
                            setattr(self.config, field.name, data[field.name])
                self.logger.info(f"Configuration loaded from {self.config_file}")
            else:
                self.logger.info("No config file found, using defaults")
                self.save_config()  # Save default config
        except Exception as e:
            self.logger.error(f"Error loading config: {e}")
            self.config = RecorderConfig()  # Reset to defaults

        return self.config

    def save_config(self) -> bool:
        """Save current configuration to file."""
        try:
            # Ensure config directory exists
            self.config_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.config_file, "w") as f:
                json.dump(asdict(self.config), f, indent=2)
            self.logger.info(f"Configuration saved to {self.config_file}")
            return True
        except Exception as e:
            self.logger.error(f"Error saving config: {e}")
            return False

    def validate_config(self) -> list[str]:
        """Validate configuration and return list of issues."""
        issues = []

        if self.config.fps <= 0 or self.config.fps > 120:
            issues.append("FPS must be between 0 and 120")

        if self.config.video_quality < 1 or self.config.video_quality > 100:
            issues.append("Video quality must be between 1 and 100")

        if self.config.audio_sample_rate not in [22050, 44100, 48000, 96000]:
            issues.append("Audio sample rate must be 22050, 44100, 48000, or 96000")

        if self.config.audio_channels not in [1, 2]:
            issues.append("Audio channels must be 1 (mono) or 2 (stereo)")

        if self.config.audio_delay_ms < -1000 or self.config.audio_delay_ms > 1000:
            issues.append("Audio delay must be between -1000 and 1000 milliseconds")

        if self.config.output_directory:
            try:
                Path(self.config.output_directory).mkdir(parents=True, exist_ok=True)
            except Exception:
                issues.append(
                    f"Cannot create output directory: {self.config.output_directory}"
                )

        return issues

    def get_available_apps(self) -> list[str]:
        """Get list of common applications that can be recorded."""
        return [
            "Firefox",
            "Chrome",
            "Chromium",
            "Brave",
            "Safari",
            "zoom",
            "Teams",
            "Skype",
            "Discord",
            "Slack",
            "Spotify",
            "VLC",
            "MPV",
            "Rhythmbox",
            "obs",
            "OBS Studio",
            "Kdenlive",
            "Audacity",
            "Code",
            "VSCode",
            "PyCharm",
            "Atom",
        ]

    def get_video_codecs(self) -> dict[str, str]:
        """Get available video codecs."""
        return {
            "XVID": "XVID (AVI)",
            "MJPG": "Motion JPEG (AVI)",
            "mp4v": "MPEG-4 (MP4)",
            "H264": "H.264 (MP4)",
            "VP80": "VP8 (WebM)",
            "VP90": "VP9 (WebM)",
        }

    def get_output_formats(self) -> list[str]:
        """Get available output formats."""
        return ["mp4", "avi", "mkv", "webm", "mov"]

    def reset_to_defaults(self):
        """Reset configuration to default values."""
        self.config = RecorderConfig()
        self.logger.info("Configuration reset to defaults")
