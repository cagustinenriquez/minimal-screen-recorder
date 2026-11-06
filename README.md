📦 ScreenRec Lite — Minimal Screen & Audio Recorder for Linux

![screenshot](snap/gui/screenshot.png) <!-- replace with your actual screenshot path or hosted image -->

ScreenRec Lite is a lightweight Python-powered screen and **system audio** recorder with a GUI, built for Linux desktops (Ubuntu, Debian, etc.).
It supports rerouting audio from apps like Firefox, Zoom, Discord, Spotify, OBS, Brave, and more — without capturing your mic.

✨ Features

- 🎥 Fullscreen screen recording
- 🔊 Record **system audio** (not just mic)
- 🖥️ GUI with per-app audio routing checkboxes
- 🧪 Built-in audio/video tests with `pytest`
- 🐧 Built for Ubuntu (also works on Debian-based distros)
- 📦 Packaged as a `.snap` and `.deb` (optional)

🧪 Installation (from Snap Store)

```bash
sudo snap install screenrec-lite
```

Or open the **Ubuntu Software Center** and search **“Screen Recorder”**.

The snap includes everything it needs (FFmpeg, PulseAudio tools, Tk), so there is nothing else to install.

🏁 Usage

After installing:

```bash
screenrec-lite
```

Or launch it from your system menu — look for **“Screen Recorder”** with the camera icon.

🔄 Updating

When a new version is released, run:

```bash
sudo snap refresh screenrec-lite
```

🐙 Development

Running from source needs Ubuntu 24.04 / Debian 13 or newer (on older releases, use
the snap). It also needs these on your system, which `./install_deps.sh` installs:
FFmpeg, PulseAudio tools (`pactl`), Tk (`python3-tk`), GStreamer with its PipeWire
plugin (for recording on Wayland), and the headers used to build PyGObject.

```bash
git clone https://github.com/cagustinenriquez/minimal-screen-recorder.git
cd minimal-screen-recorder
./install_deps.sh
uv run app.py
```

🧪 Run tests

```bash
pytest
```

🧰 Packaging

🔹 Build the snap (needs LXD; the recipe is in `snap/snapcraft.yaml`):

```bash
snapcraft pack
sudo snap install --dangerous ./screenrec-lite_*_amd64.snap
```

🔹 Build `.deb` (coming soon)

📤 Publish to Snap Store

> Only required for maintainers

```bash
snapcraft upload screenrec-lite_*_amd64.snap --release=stable
```

📝 License

MIT © Carlos Agustín Enríquez (https://github.com/cagustinenriquez)
