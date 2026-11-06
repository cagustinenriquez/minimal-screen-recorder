"""Screen capture on Wayland through the xdg-desktop-portal ScreenCast API.

Wayland does not let apps read the screen directly (mss returns black frames),
so we ask the desktop for a screen-share stream (the same "Share screen" prompt
browsers use) and read frames from it with GStreamer's pipewiresrc.
"""

import logging
import os
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"

SOURCE_MONITOR = 1
CURSOR_EMBEDDED = 2
PERSIST_UNTIL_REVOKED = 2


class PortalCaptureError(Exception):
    """Screen sharing could not be started."""


def is_wayland_session() -> bool:
    """True when running under Wayland, where direct screen capture is blocked."""
    return bool(os.environ.get("WAYLAND_DISPLAY")) or (
        os.environ.get("XDG_SESSION_TYPE") == "wayland"
    )


def _token_file() -> Path:
    base = os.environ.get("SNAP_USER_DATA") or str(
        Path.home() / ".config" / "screenrec-lite"
    )
    return Path(base) / "screencast_restore_token"


def _load_restore_token() -> str | None:
    try:
        token = _token_file().read_text().strip()
        return token or None
    except OSError:
        return None


def _save_restore_token(token: str | None) -> None:
    if not token:
        return
    try:
        path = _token_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token)
    except OSError as e:
        logger.warning(f"Could not save screen-share permission: {e}")


class PortalFrameSource:
    """Frames from a portal screen-cast stream, as BGR numpy arrays.

    Use as a context manager. Opening shows the desktop's "Share screen"
    dialog the first time; after that the choice is remembered.
    """

    def __init__(self, response_timeout: float = 120.0):
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("GstApp", "1.0")
        from gi.repository import Gio, GLib, Gst

        self.Gio, self.GLib, self.Gst = Gio, GLib, Gst
        Gst.init(None)
        self.response_timeout = response_timeout
        self.bus = None
        self.session_handle: str | None = None
        self.pipeline = None
        self.appsink = None
        self.pw_fd: int | None = None
        self._token_counter = 0
        self._latest: np.ndarray | None = None
        self._frame_lock = threading.Lock()
        self._frame_ready = threading.Event()
        self._stream_size: tuple[int, int] | None = None

    # ---- portal (D-Bus) -------------------------------------------------

    def _new_token(self) -> str:
        self._token_counter += 1
        return f"screenrec{os.getpid()}_{self._token_counter}"

    def _request_path(self, token: str) -> str:
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    def _call_and_wait(self, method: str, params) -> dict:
        """Call a portal method and wait for its Request.Response signal."""
        GLib = self.GLib
        token = params.unpack()[-1]["handle_token"]
        context = self._context
        result: dict = {}

        def on_response(_conn, _sender, _path, _iface, _signal, value):
            code, results = value.unpack()
            result["code"] = code
            result["results"] = results

        # Subscribe before calling so a fast response is never missed.
        sub_id = self.bus.signal_subscribe(
            PORTAL_BUS,
            REQUEST_IFACE,
            "Response",
            self._request_path(token),
            None,
            self.Gio.DBusSignalFlags.NO_MATCH_RULE,
            on_response,
        )
        try:
            self.bus.call_sync(
                PORTAL_BUS,
                PORTAL_PATH,
                SCREENCAST_IFACE,
                method,
                params,
                None,
                self.Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            deadline = GLib.get_monotonic_time() + int(self.response_timeout * 1e6)
            while "code" not in result:
                if GLib.get_monotonic_time() > deadline:
                    raise PortalCaptureError(
                        f"No answer from the screen-share dialog ({method})"
                    )
                context.iteration(True)
        finally:
            self.bus.signal_unsubscribe(sub_id)

        if result["code"] == 1:
            raise PortalCaptureError("Screen sharing was cancelled")
        if result["code"] != 0:
            raise PortalCaptureError(f"Screen sharing failed ({method})")
        return result["results"]

    def _start_session(self) -> int:
        """Run the portal handshake and return the PipeWire node id."""
        GLib, Gio = self.GLib, self.Gio
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        results = self._call_and_wait(
            "CreateSession",
            GLib.Variant(
                "(a{sv})",
                (
                    {
                        "handle_token": GLib.Variant("s", self._new_token()),
                        "session_handle_token": GLib.Variant("s", self._new_token()),
                    },
                ),
            ),
        )
        self.session_handle = results["session_handle"]

        select_opts = {
            "handle_token": GLib.Variant("s", self._new_token()),
            "types": GLib.Variant("u", SOURCE_MONITOR),
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", CURSOR_EMBEDDED),
            "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
        }
        restore_token = _load_restore_token()
        if restore_token:
            select_opts["restore_token"] = GLib.Variant("s", restore_token)
        self._call_and_wait(
            "SelectSources",
            GLib.Variant("(oa{sv})", (self.session_handle, select_opts)),
        )

        results = self._call_and_wait(
            "Start",
            GLib.Variant(
                "(osa{sv})",
                (
                    self.session_handle,
                    "",
                    {"handle_token": GLib.Variant("s", self._new_token())},
                ),
            ),
        )
        _save_restore_token(results.get("restore_token"))
        streams = results.get("streams") or []
        if not streams:
            raise PortalCaptureError("No screen was shared")
        node_id, props = streams[0]
        if "size" in props:
            self._stream_size = tuple(props["size"])

        reply, fd_list = self.bus.call_with_unix_fd_list_sync(
            PORTAL_BUS,
            PORTAL_PATH,
            SCREENCAST_IFACE,
            "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self.session_handle, {})),
            GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            None,
        )
        self.pw_fd = fd_list.get(reply.unpack()[0])
        return node_id

    def _close_session(self) -> None:
        if self.bus and self.session_handle:
            try:
                self.bus.call_sync(
                    PORTAL_BUS,
                    self.session_handle,
                    "org.freedesktop.portal.Session",
                    "Close",
                    None,
                    None,
                    self.Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                )
            except Exception as e:
                logger.debug(f"Closing screen-share session: {e}")
        self.session_handle = None

    # ---- GStreamer ------------------------------------------------------

    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return self.Gst.FlowReturn.OK
        caps = sample.get_caps().get_structure(0)
        width, height = caps.get_value("width"), caps.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(self.Gst.MapFlags.READ)
        if ok:
            try:
                data = np.frombuffer(info.data, dtype=np.uint8)
                stride = len(data) // height
                frame = data.reshape(height, stride)[:, : width * 3]
                frame = frame.reshape(height, width, 3).copy()
            finally:
                buf.unmap(info)
            with self._frame_lock:
                self._latest = frame
            self._frame_ready.set()
        return self.Gst.FlowReturn.OK

    def _start_pipeline(self, node_id: int) -> None:
        Gst = self.Gst
        self.pipeline = Gst.parse_launch(
            f"pipewiresrc fd={self.pw_fd} path={node_id} do-timestamp=true "
            "keepalive-time=1000 always-copy=true ! videoconvert ! "
            "video/x-raw,format=BGR ! "
            "appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
        )
        self.appsink = self.pipeline.get_by_name("sink")
        self.appsink.connect("new-sample", self._on_new_sample)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise PortalCaptureError("Could not start the screen-share stream")

    # ---- public API -----------------------------------------------------

    def open(self) -> "PortalFrameSource":
        if self.pipeline is not None:
            return self
        # Portal replies arrive as D-Bus signals; handle them on a context
        # owned by this thread so we never touch anyone else's main loop.
        self._context = self.GLib.MainContext.new()
        self._context.push_thread_default()
        try:
            node_id = self._start_session()
            self._start_pipeline(node_id)
            # GNOME only sends frames when something on screen changes, so a
            # still screen may send nothing yet. Wait briefly; don't fail.
            if not self._frame_ready.wait(timeout=2) and self._stream_size is None:
                raise PortalCaptureError("The shared screen sent no picture")
        except Exception:
            self.close()
            raise
        finally:
            self._context.pop_thread_default()
        return self

    def grab(self) -> np.ndarray:
        """Return the most recent frame (BGR); black until the first arrives."""
        with self._frame_lock:
            if self._latest is None:
                width, height = self.size
                return np.zeros((height, width, 3), dtype=np.uint8)
            return self._latest

    @property
    def size(self) -> tuple[int, int]:
        with self._frame_lock:
            if self._latest is not None:
                return (self._latest.shape[1], self._latest.shape[0])
        return self._stream_size

    def close(self) -> None:
        if self.pipeline is not None:
            self.pipeline.set_state(self.Gst.State.NULL)
            self.pipeline = None
        self._close_session()
        if self.pw_fd is not None:
            try:
                os.close(self.pw_fd)
            except OSError:
                pass
            self.pw_fd = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False
