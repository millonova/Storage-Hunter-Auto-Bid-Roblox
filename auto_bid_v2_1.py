"""Low-latency outline-triggered bid macro controller.

This optimized alternative leaves ``auto_bid_v2.py`` unchanged.  It uses the
same movable frame and controls, but takes the shortest practical path from
screen capture to keypress:

* checks only thin bands around the selected panel's outside edge;
* coalesces UI/debug work so it never runs on every scan;
* requests Windows' 1 ms timer resolution only while the macro is active;
* uses deadline scheduling rather than adding a sleep after each capture.

Use only where this kind of automation is permitted by the game's rules.
"""

from __future__ import annotations

import ctypes
import json
import sys
import time
import tkinter as tk
from pathlib import Path

import mss
import numpy as np
from pynput import keyboard

from auto_bid_v2 import BidOutlineApp


APP_DIR = Path(__file__).resolve().parent
SETTINGS_DIR = APP_DIR / "settings"
SETTINGS_FILE = SETTINGS_DIR / "auto_bid_v2_1_settings.json"
DEFAULTS = {
    "left": 6,
    "top": 7,
    "width": 808,
    "height": 136,
    "key": "q",
    # 2 ms is a target interval. Actual rate also depends on GPU/display/capture.
    "poll_ms": 2,
    "cooldown_ms": 150,
    "minimum_edge_pixels": 550,
    "edge_band_px": 12,
    "always_on_top": True,
}


class HighResolutionTimer:
    """Temporarily request a 1 ms Windows scheduling resolution."""

    def __init__(self) -> None:
        self.enabled = False

    def __enter__(self) -> "HighResolutionTimer":
        if sys.platform == "win32":
            try:
                self.enabled = ctypes.windll.winmm.timeBeginPeriod(1) == 0
            except (AttributeError, OSError):
                self.enabled = False
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self.enabled:
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except (AttributeError, OSError):
                pass


def bright_pixels_bgr(pixels: np.ndarray) -> int:
    """Count pale/bright outline pixels in a BGR image strip.

    This deliberately avoids a BGR-to-RGB conversion and avoids creating a
    whole-panel brightness mask.  The colour rule keeps green Bid/Q text out of
    the count while retaining the reference image's pale green-white outline.
    """
    blue = pixels[..., 0].astype(np.uint16)
    green = pixels[..., 1].astype(np.uint16)
    red = pixels[..., 2].astype(np.uint16)
    bright = (red + green + blue >= 495) & (red >= 135) & (green >= 160) & (blue >= 135)
    return int(np.count_nonzero(bright))


def fast_outline_stats_bgr(frame: np.ndarray, edge_band: int) -> tuple[int, int, int, int]:
    """Count bright pixels in the four edge bands, processing no center pixels."""
    height, width = frame.shape[:2]
    band = max(1, min(edge_band, height // 3, width // 3))
    top = bright_pixels_bgr(frame[:band, :, :3])
    bottom = bright_pixels_bgr(frame[-band:, :, :3])
    # Exclude the horizontal bands to avoid double-counting corner pixels.
    middle = frame[band:-band, :, :3]
    left = bright_pixels_bgr(middle[:, :band, :])
    right = bright_pixels_bgr(middle[:, -band:, :])
    return top, bottom, left, right


class OptimizedBidOutlineApp(BidOutlineApp):
    """V2 UI with a low-latency capture-to-key path and coalesced diagnostics."""

    def __init__(self, root: tk.Tk) -> None:
        # These values are written by the capture worker and painted at 10 Hz by
        # the Tk main thread.  No Tk calls are queued once per captured frame.
        self._latest_summary = "No frames captured yet."
        self._latest_status = "Stopped - set the frame, then Start."
        super().__init__(root)

    @staticmethod
    def load_settings() -> dict:
        settings = DEFAULTS.copy()
        try:
            settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            pass
        return settings

    def save_settings(self) -> None:
        try:
            self.config = self.read_ui_config()
            SETTINGS_DIR.mkdir(exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        except ValueError:
            pass

    def update_status(self, text: str) -> None:
        """Worker-safe status update; the UI applies it during its next refresh."""
        self._latest_status = text

    def set_debug_summary(self, text: str) -> None:
        """Worker-safe summary update without an unbounded Tk event queue."""
        self._latest_summary = text

    def flush_debug(self) -> None:
        """Refresh coalesced status/summary, then let the shared logger flush."""
        if self.status.get() != self._latest_status:
            self.status.set(self._latest_status)
        if self.debug_summary.get() != self._latest_summary:
            self.debug_summary.set(self._latest_summary)
        super().flush_debug()

    def capture_loop(self) -> None:
        controller = keyboard.Controller()
        armed = True
        last_press = 0.0
        previous_active: bool | None = None
        last_ui_refresh = 0.0
        rate_started = time.perf_counter()
        frames_since_rate = 0
        measured_hz = 0.0
        next_deadline = time.perf_counter()

        with HighResolutionTimer() as timer, mss.mss() as capture:
            timer_text = "enabled" if timer.enabled else "unavailable"
            self.log_debug(f"START OPTIMIZED: 1 ms Windows timer {timer_text}; edge-strip detector active.")
            while self.running.is_set():
                with self.config_lock:
                    config = self.config.copy()
                region = {name: config[name] for name in ("left", "top", "width", "height")}
                capture_started = time.perf_counter()
                try:
                    frame = np.asarray(capture.grab(region))
                except mss.exception.ScreenShotError as exc:
                    self.update_status(f"Capture error: {exc}")
                    self.set_debug_summary("CAPTURE ERROR - see report below.")
                    self.log_debug(f"CAPTURE ERROR: {exc}")
                    time.sleep(0.05)
                    continue

                top, bottom, left, right = fast_outline_stats_bgr(frame, config["edge_band_px"])
                edge_total = top + bottom + left + right
                active = edge_total >= config["minimum_edge_pixels"]
                now = time.perf_counter()
                capture_ms = (now - capture_started) * 1000
                frames_since_rate += 1
                elapsed_for_rate = now - rate_started
                if elapsed_for_rate >= 1.0:
                    measured_hz = frames_since_rate / elapsed_for_rate
                    frames_since_rate = 0
                    rate_started = now

                summary = (
                    f"OUTLINE {'BRIGHT' if active else 'DIM'} | edge: {edge_total}/"
                    f"{config['minimum_edge_pixels']} px | top/bottom/left/right: "
                    f"{top}/{bottom}/{left}/{right} | capture+detect: {capture_ms:.1f} ms | "
                    f"scan rate: {measured_hz:.0f} Hz"
                )
                if previous_active is None or active != previous_active:
                    self.log_debug(summary + f" | state changed to {'BRIGHT' if active else 'DIM'}")
                    previous_active = active

                if active and armed and (now - last_press) * 1000 >= config["cooldown_ms"]:
                    controller.press(config["key"])
                    controller.release(config["key"])
                    armed, last_press = False, now
                    self.set_debug_summary(summary + f" | KEY SENT: {config['key'].upper()}")
                    self.update_status(f"{config['key'].upper()} sent - bright outline detected.")
                    self.log_debug(summary + f" | KEY SENT: {config['key'].upper()}")
                elif not active:
                    armed = True
                    self.update_status("Running - waiting for the bright outer outline...")

                # A 10 Hz UI refresh is enough to read diagnostics but cannot
                # get in the way of a 2 ms scanner.
                if now - last_ui_refresh >= 0.10:
                    self.set_debug_summary(summary + (" | armed" if armed else " | waiting to re-arm"))
                    last_ui_refresh = now

                # Deadline scheduling avoids adding a full sleep *after* an
                # expensive capture. If capture already used the budget, scan
                # again immediately instead of falling behind.
                interval = config["poll_ms"] / 1000.0
                next_deadline += interval
                if next_deadline <= now:
                    next_deadline = now
                else:
                    time.sleep(next_deadline - now)

        self.log_debug("STOP OPTIMIZED: high-resolution timer released.")


def main() -> None:
    root = tk.Tk()
    app = OptimizedBidOutlineApp(root)
    root.title("Bid Outline Macro (V2.1 - Optimized)")
    root.mainloop()


if __name__ == "__main__":
    main()
