#!/bin/bash

# Install system dependencies for minimal-screen-recorder
# This script installs dependencies that are not available via pip

set -e

echo "Installing system dependencies for minimal-screen-recorder..."

# Detect package manager
if command -v apt &> /dev/null; then
    echo "Using apt package manager..."
    
    # Update package list
    echo "Updating package list..."
    sudo apt update
    
    # Install system dependencies
    echo "Installing FFmpeg..."
    sudo apt install -y ffmpeg
    
    echo "Installing PulseAudio utilities..."
    sudo apt install -y pulseaudio-utils
    
    echo "Installing tkinter (if not already present)..."
    sudo apt install -y python3-tk

    echo "Installing Wayland screen capture support (PyGObject, GStreamer, PipeWire)..."
    sudo apt install -y python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
        gstreamer1.0-plugins-base gstreamer1.0-pipewire
    # Headers so uv/pip can build PyGObject inside the virtualenv
    sudo apt install -y pkg-config libgirepository-2.0-dev libcairo2-dev
    
elif command -v dnf &> /dev/null; then
    echo "Using dnf package manager..."
    
    echo "Installing FFmpeg..."
    sudo dnf install -y ffmpeg
    
    echo "Installing PulseAudio utilities..."
    sudo dnf install -y pulseaudio-utils
    
    echo "Installing tkinter..."
    sudo dnf install -y python3-tkinter

    echo "Installing Wayland screen capture support (PyGObject, GStreamer, PipeWire)..."
    sudo dnf install -y python3-gobject gstreamer1-plugins-base pipewire-gstreamer
    sudo dnf install -y pkgconf gobject-introspection-devel cairo-devel
    
elif command -v pacman &> /dev/null; then
    echo "Using pacman package manager..."
    
    echo "Installing FFmpeg..."
    sudo pacman -S --noconfirm ffmpeg
    
    echo "Installing PulseAudio utilities (pactl)..."
    sudo pacman -S --noconfirm libpulse
    
    echo "Installing tkinter..."
    sudo pacman -S --noconfirm tk

    echo "Installing Wayland screen capture support (PyGObject, GStreamer, PipeWire)..."
    sudo pacman -S --noconfirm python-gobject gst-plugins-base gst-plugin-pipewire
    sudo pacman -S --noconfirm pkgconf gobject-introspection cairo
    
else
    echo "Unsupported package manager. Please install manually:"
    echo "- FFmpeg"
    echo "- PulseAudio utilities"
    echo "- Python3 tkinter"
    echo "- PyGObject, GStreamer (plugins-base) and the GStreamer PipeWire plugin"
    exit 1
fi

echo ""
echo "System dependencies installed successfully!"
echo "Now run the app with: uv run app.py"