"""Outline-triggered bid macro controller.

This is the alternative to ``auto_bid.py`` for a Bid panel whose outer border
changes from dim grey to a bright pale outline. It captures only a selected
screen rectangle and sends the configured key once per bright-outline event.
"""

from __future__ import annotations

import json
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import mss
import numpy as np
from pynput import keyboard

from auto_bid import MIN_HEIGHT, MIN_WIDTH, MacroApp


APP_DIR = Path(__file__).resolve().parent
SETTINGS_DIR = APP_DIR / "settings"
SETTINGS_FILE = SETTINGS_DIR / "auto_bid_v2_settings.json"
DEFAULTS = {
    "left": 6, "top": 7, "width": 808, "height": 136,
    "key": "q", "poll_ms": 8, "cooldown_ms": 150,
    "minimum_edge_pixels": 550, "edge_band_px": 12, "always_on_top": True,
}


def bright_outline_stats(rgb: np.ndarray, edge_band: int) -> tuple[int, int, int, int, int]:
    """Return all bright pixels plus bright pixels on each capture-frame edge.

    The trigger outline in the reference is pale green-white. Checking red and
    blue as well as brightness rejects the green Bid/Q lettering. Restricting
    the trigger count to the outer band rejects the persistent inner Q border.
    """
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    luminance = (r.astype(np.uint16) + g.astype(np.uint16) + b.astype(np.uint16)) / 3
    bright = (luminance >= 165) & (r >= 135) & (g >= 160) & (b >= 135)
    height, width = bright.shape
    band = max(1, min(edge_band, height // 3, width // 3))
    top = int(np.count_nonzero(bright[:band, :]))
    bottom = int(np.count_nonzero(bright[-band:, :]))
    left = int(np.count_nonzero(bright[:, :band]))
    right = int(np.count_nonzero(bright[:, -band:]))
    return int(np.count_nonzero(bright)), top, bottom, left, right


class BidOutlineApp(MacroApp):
    """Reuse the capture-frame/debug machinery with outline detection."""

    @staticmethod
    def load_settings() -> dict:
        settings = DEFAULTS.copy()
        try:
            settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            pass
        return settings

    def build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        content = ttk.Frame(self.root, padding=10)
        content.grid(sticky="nsew")
        ttk.Label(content, text="Bid panel capture frame", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 4)
        )
        for i, (label, name) in enumerate(
            (("X", "left"), ("Y", "top"), ("Width", "width"), ("Height", "height"))
        ):
            ttk.Label(content, text=label).grid(row=1, column=i, **pad)
            ttk.Entry(content, textvariable=self.vars[name], width=8).grid(row=2, column=i, **pad)
        ttk.Button(content, text="Apply / show frame", command=self.show_frame).grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 4)
        )
        ttk.Button(content, text="Hide frame", command=lambda: self.overlay.hide()).grid(
            row=3, column=2, columnspan=2, sticky="ew", padx=8, pady=(8, 4)
        )

        ttk.Separator(content).grid(row=4, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Label(content, text="Outline trigger settings", font=("Segoe UI", 10, "bold")).grid(
            row=5, column=0, columnspan=4, sticky="w")
        fields = (
            ("Key", "key"), ("Scan every (ms)", "poll_ms"),
            ("Cooldown (ms)", "cooldown_ms"),
            ("Bright edge pixels", "minimum_edge_pixels"), ("Edge band (px)", "edge_band_px"),
        )
        for row, (label, name) in enumerate(fields, start=6):
            ttk.Label(content, text=label).grid(row=row, column=0, columnspan=2, sticky="w", **pad)
            ttk.Entry(content, textvariable=self.vars[name], width=12).grid(
                row=row, column=2, columnspan=2, sticky="ew", **pad
            )
        ttk.Checkbutton(content, text="Keep controller on top", variable=self.on_top,
                        command=self.apply_always_on_top).grid(
            row=11, column=0, columnspan=4, sticky="w", **pad
        )
        buttons = ttk.Frame(content)
        buttons.grid(row=12, column=0, columnspan=4, sticky="ew", pady=(8, 4))
        self.start_button = ttk.Button(buttons, text="Start macro", command=self.start)
        self.start_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.stop_button = ttk.Button(buttons, text="Stop (F8)", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(content, text="Test configured key", command=self.test_key).grid(
            row=13, column=0, columnspan=2, sticky="ew", padx=8, pady=(5, 0)
        )
        ttk.Button(content, text="Save capture image", command=self.save_capture).grid(
            row=13, column=2, columnspan=2, sticky="ew", padx=8, pady=(5, 0)
        )
        ttk.Label(content, textvariable=self.status, wraplength=390, foreground="#176b3a").grid(
            row=14, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )
        ttk.Separator(content).grid(row=15, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Label(content, text="Live debug report", font=("Segoe UI", 10, "bold")).grid(
            row=16, column=0, columnspan=3, sticky="w"
        )
        ttk.Button(content, text="Clear", command=self.clear_debug).grid(row=16, column=3, sticky="e")
        ttk.Label(content, textvariable=self.debug_summary, wraplength=390).grid(
            row=17, column=0, columnspan=4, sticky="w", pady=(3, 4)
        )
        self.debug_output = tk.Text(content, width=54, height=9, state="disabled", wrap="word",
                                    bg="#101511", fg="#bbf7d0", insertbackground="#bbf7d0",
                                    font=("Consolas", 9), relief="flat")
        self.debug_output.grid(row=18, column=0, columnspan=4, sticky="ew")

    def read_ui_config(self) -> dict:
        try:
            left, top, width, height = self.frame_values()
            poll_ms = int(self.vars["poll_ms"].get())
            cooldown_ms = int(self.vars["cooldown_ms"].get())
            minimum_edge_pixels = int(self.vars["minimum_edge_pixels"].get())
            edge_band_px = int(self.vars["edge_band_px"].get())
        except ValueError as exc:
            raise ValueError("All coordinate and trigger fields must be whole numbers.") from exc
        key = self.vars["key"].get().strip().lower()
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            raise ValueError(f"Frame must be at least {MIN_WIDTH}x{MIN_HEIGHT} pixels.")
        if len(key) != 1:
            raise ValueError("Key must be a single character, such as q.")
        if poll_ms < 1 or cooldown_ms < 0 or minimum_edge_pixels < 1 or edge_band_px < 1:
            raise ValueError("Scan interval, bright pixels, and edge band must be at least 1; cooldown cannot be negative.")
        return {
            "left": left, "top": top, "width": width, "height": height, "key": key,
            "poll_ms": poll_ms, "cooldown_ms": cooldown_ms,
            "minimum_edge_pixels": minimum_edge_pixels, "edge_band_px": edge_band_px,
            "always_on_top": self.on_top.get(),
        }

    def save_settings(self) -> None:
        try:
            self.config = self.read_ui_config()
            SETTINGS_DIR.mkdir(exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        except ValueError:
            pass

    def capture_loop(self) -> None:
        controller = keyboard.Controller()
        armed = True
        last_press = 0.0
        last_report = 0.0
        with mss.mss() as capture:
            while self.running.is_set():
                with self.config_lock:
                    config = self.config.copy()
                region = {name: config[name] for name in ("left", "top", "width", "height")}
                try:
                    frame = np.asarray(capture.grab(region))[:, :, :3]
                except mss.exception.ScreenShotError as exc:
                    self.update_status(f"Capture error: {exc}")
                    self.log_debug(f"CAPTURE ERROR: {exc}")
                    time.sleep(0.2)
                    continue
                total, top, bottom, left, right = bright_outline_stats(
                    frame[:, :, ::-1], config["edge_band_px"]
                )
                edge_total = top + bottom + left + right
                active = edge_total >= config["minimum_edge_pixels"]
                now = time.monotonic()
                summary = (
                    f"OUTLINE {'BRIGHT' if active else 'DIM'} | edge: {edge_total}/"
                    f"{config['minimum_edge_pixels']} px | top/bottom/left/right: "
                    f"{top}/{bottom}/{left}/{right} | all bright: {total}"
                )
                if active and armed and (now - last_press) * 1000 >= config["cooldown_ms"]:
                    controller.press(config["key"])
                    controller.release(config["key"])
                    armed, last_press = False, now
                    self.set_debug_summary(summary + f" | KEY SENT: {config['key'].upper()}")
                    self.log_debug(summary + f" | KEY SENT: {config['key'].upper()}")
                    self.update_status(f"{config['key'].upper()} sent - bright outline detected.")
                elif not active:
                    armed = True
                    self.update_status("Running - waiting for the bright outer outline...")
                if now - last_report >= 0.25:
                    self.set_debug_summary(summary)
                    self.log_debug(summary + (" | armed" if armed else " | waiting to re-arm"))
                    last_report = now
                time.sleep(config["poll_ms"] / 1000)


def main() -> None:
    root = tk.Tk()
    root.title("Bid Outline Macro (V2)")
    BidOutlineApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
