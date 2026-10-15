"""Desktop controller for the screen-based timing-bar macro.

The macro captures only the transparent frame selected in the UI. It searches
that frame for a saturated green target and a tall white moving marker, then
sends the configured key when the marker overlaps the target.

Use only where this type of automation is permitted by the game's rules.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import mss
import numpy as np
from pynput import keyboard
from mss import tools as mss_tools


APP_DIR = Path(__file__).resolve().parent
SETTINGS_DIR = APP_DIR / "settings"
SETTINGS_FILE = SETTINGS_DIR / "auto_bid_settings.json"
OVERLAY_TRANSPARENT = "#ff00ff"
MIN_WIDTH, MIN_HEIGHT = 80, 30

DEFAULTS = {
    "left": 91,
    "top": 98,
    "width": 924,
    "height": 78,
    "key": "q",
    "edge_margin": 3,
    "poll_ms": 8,
    "cooldown_ms": 150,
    "always_on_top": True,
}


def horizontal_runs(columns: np.ndarray) -> list[tuple[int, int]]:
    """Return all inclusive horizontal runs of True values."""
    starts = np.flatnonzero(columns & ~np.r_[False, columns[:-1]])
    ends = np.flatnonzero(columns & ~np.r_[columns[1:], False])
    return [(int(start), int(end)) for start, end in zip(starts, ends)]


def largest_horizontal_run(columns: np.ndarray) -> tuple[int, int] | None:
    """Return the longest inclusive run of True values in a 1-D array."""
    runs = horizontal_runs(columns)
    if not runs:
        return None
    return max(runs, key=lambda run: run[1] - run[0])


def find_target_and_marker(rgb: np.ndarray) -> tuple[tuple[int, int], int, str] | None:
    """Locate target/marker, including when the marker covers green pixels.

    The original detector picked only the longest green run. That breaks at the
    moment that matters: the white marker splits the green target into left and
    right runs. This merges those runs across a narrow white-marker gap.
    """
    if rgb.shape[0] < MIN_HEIGHT or rgb.shape[1] < MIN_WIDTH:
        return None
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    # Saturated green target. The required vertical coverage rejects labels.
    green = (g > 115) & (g > r * 1.45) & (g > b * 1.25)
    green_runs = horizontal_runs(green.sum(axis=0) >= rgb.shape[0] * 0.55)

    # Near-white marker, required to span most of the selected bar's height.
    white = (r > 215) & (g > 215) & (b > 215)
    marker = largest_horizontal_run(white.sum(axis=0) >= rgb.shape[0] * 0.65)
    if not green_runs or marker is None:
        return None
    marker_x = (marker[0] + marker[1]) // 2

    # Merge the green runs directly on either side of the tall white marker.
    # This reconstructs the target while it is partially hidden by the marker.
    left_runs = [run for run in green_runs if run[1] < marker[0]]
    right_runs = [run for run in green_runs if run[0] > marker[1]]
    if left_runs and right_runs:
        left = max(left_runs, key=lambda run: run[1])
        right = min(right_runs, key=lambda run: run[0])
        gap = right[0] - left[1] - 1
        if gap <= max(28, marker[1] - marker[0] + 12):
            return (left[0], right[1]), marker_x, "merged around marker"

    target = max(green_runs, key=lambda run: run[1] - run[0])
    return target, marker_x, "single green run"


class SelectionOverlay:
    """A border-only, draggable and resizable screen selection frame."""

    def __init__(self, app: "MacroApp") -> None:
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=OVERLAY_TRANSPARENT)
        self.window.attributes("-transparentcolor", OVERLAY_TRANSPARENT)
        self.canvas = tk.Canvas(
            self.window, bg=OVERLAY_TRANSPARENT, highlightthickness=0,
            bd=0, cursor="fleur"
        )
        self.canvas.pack(fill="both", expand=True)
        self.drag: tuple[str, int, int, int, int, int, int] | None = None
        self.canvas.bind("<ButtonPress-1>", self.begin_drag)
        self.canvas.bind("<B1-Motion>", self.drag_frame)
        self.canvas.bind("<ButtonRelease-1>", self.end_drag)
        self.window.bind("<Escape>", lambda _event: self.hide())

    def show(self) -> None:
        self.sync_from_app()
        self.window.deiconify()
        self.window.lift()

    def hide(self) -> None:
        self.window.withdraw()

    def sync_from_app(self) -> None:
        left, top, width, height = self.app.frame_values()
        self.window.geometry(f"{width}x{height}+{left}+{top}")
        self.draw(width, height)

    def draw(self, width: int, height: int) -> None:
        self.canvas.delete("all")
        # The inside remains transparent. Only the outline appears on screen.
        self.canvas.create_rectangle(1, 1, width - 2, height - 2, outline="#00ff75", width=3)
        handle = 7
        self.canvas.create_rectangle(width - handle, height - handle, width - 1, height - 1,
                                     fill="#00ff75", outline="")

    def begin_drag(self, event: tk.Event) -> None:
        left, top, width, height = self.app.frame_values()
        edge = 14
        if event.x >= width - edge and event.y >= height - edge:
            mode = "resize"
            self.canvas.configure(cursor="size_nw_se")
        else:
            mode = "move"
            self.canvas.configure(cursor="fleur")
        self.drag = (mode, event.x_root, event.y_root, left, top, width, height)

    def drag_frame(self, event: tk.Event) -> None:
        if self.drag is None:
            return
        mode, start_x, start_y, left, top, width, height = self.drag
        dx, dy = event.x_root - start_x, event.y_root - start_y
        if mode == "move":
            left, top = left + dx, top + dy
        else:
            width, height = max(MIN_WIDTH, width + dx), max(MIN_HEIGHT, height + dy)
        self.app.set_frame_values(left, top, width, height, save=False)
        self.sync_from_app()

    def end_drag(self, _event: tk.Event) -> None:
        self.drag = None
        self.canvas.configure(cursor="fleur")
        self.app.save_settings()


class MacroApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Storage Hunter Auto Bid Roblox (Timing Bar)")
        self.root.resizable(False, False)
        self.config = self.load_settings()
        self.config_lock = threading.Lock()
        self.running = threading.Event()
        self.worker: threading.Thread | None = None
        self.status = tk.StringVar(value="Stopped - set the frame, then Start.")
        self.debug_summary = tk.StringVar(value="No frames captured yet.")
        self.debug_queue: queue.Queue[str] = queue.Queue(maxsize=80)
        self.vars = {name: tk.StringVar(value=str(value)) for name, value in self.config.items()
                     if name != "always_on_top"}
        self.on_top = tk.BooleanVar(value=bool(self.config["always_on_top"]))

        self.build_ui()
        self.overlay = SelectionOverlay(self)
        self.listener = keyboard.Listener(on_press=self.hotkey)
        self.listener.start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.apply_always_on_top()
        self.root.after(120, self.flush_debug)

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

        ttk.Label(content, text="Capture frame", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 4)
        )
        for i, (label, name) in enumerate((("X", "left"), ("Y", "top"), ("Width", "width"), ("Height", "height"))):
            ttk.Label(content, text=label).grid(row=1, column=i, **pad)
            ttk.Entry(content, textvariable=self.vars[name], width=8).grid(row=2, column=i, **pad)

        ttk.Button(content, text="Apply / show frame", command=self.show_frame).grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 4)
        )
        ttk.Button(content, text="Hide frame", command=lambda: self.overlay.hide()).grid(
            row=3, column=2, columnspan=2, sticky="ew", padx=8, pady=(8, 4)
        )

        ttk.Separator(content).grid(row=4, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Label(content, text="Timing settings", font=("Segoe UI", 10, "bold")).grid(
            row=5, column=0, columnspan=4, sticky="w")
        fields = (("Key", "key"), ("Edge margin (px)", "edge_margin"),
                  ("Scan every (ms)", "poll_ms"), ("Cooldown (ms)", "cooldown_ms"))
        for row, (label, name) in enumerate(fields, start=6):
            ttk.Label(content, text=label).grid(row=row, column=0, columnspan=2, sticky="w", **pad)
            ttk.Entry(content, textvariable=self.vars[name], width=12).grid(
                row=row, column=2, columnspan=2, sticky="ew", **pad
            )

        ttk.Checkbutton(content, text="Keep controller on top", variable=self.on_top,
                        command=self.apply_always_on_top).grid(
            row=10, column=0, columnspan=4, sticky="w", **pad
        )
        buttons = ttk.Frame(content)
        buttons.grid(row=11, column=0, columnspan=4, sticky="ew", pady=(8, 4))
        self.start_button = ttk.Button(buttons, text="Start macro", command=self.start)
        self.start_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.stop_button = ttk.Button(buttons, text="Stop (F8)", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(content, text="Test configured key", command=self.test_key).grid(
            row=12, column=0, columnspan=2, sticky="ew", padx=8, pady=(5, 0)
        )
        ttk.Button(content, text="Save capture image", command=self.save_capture).grid(
            row=12, column=2, columnspan=2, sticky="ew", padx=8, pady=(5, 0)
        )
        ttk.Label(content, textvariable=self.status, wraplength=390, foreground="#176b3a").grid(
            row=13, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )
        ttk.Separator(content).grid(row=14, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Label(content, text="Live debug report", font=("Segoe UI", 10, "bold")).grid(
            row=15, column=0, columnspan=3, sticky="w"
        )
        ttk.Button(content, text="Clear", command=self.clear_debug).grid(row=15, column=3, sticky="e")
        ttk.Label(content, textvariable=self.debug_summary, wraplength=390).grid(
            row=16, column=0, columnspan=4, sticky="w", pady=(3, 4)
        )
        self.debug_output = tk.Text(content, width=54, height=9, state="disabled", wrap="word",
                                    bg="#101511", fg="#bbf7d0", insertbackground="#bbf7d0",
                                    font=("Consolas", 9), relief="flat")
        self.debug_output.grid(row=17, column=0, columnspan=4, sticky="ew")

    def frame_values(self) -> tuple[int, int, int, int]:
        return tuple(int(self.vars[name].get()) for name in ("left", "top", "width", "height"))

    def set_frame_values(self, left: int, top: int, width: int, height: int, save: bool) -> None:
        for name, value in zip(("left", "top", "width", "height"), (left, top, width, height)):
            self.vars[name].set(str(value))
        if save:
            self.save_settings()

    def read_ui_config(self) -> dict:
        try:
            left, top, width, height = self.frame_values()
            edge_margin = int(self.vars["edge_margin"].get())
            poll_ms = int(self.vars["poll_ms"].get())
            cooldown_ms = int(self.vars["cooldown_ms"].get())
        except ValueError as exc:
            raise ValueError("All coordinate and timing fields must be whole numbers.") from exc
        key = self.vars["key"].get().strip().lower()
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            raise ValueError(f"Frame must be at least {MIN_WIDTH}×{MIN_HEIGHT} pixels.")
        if len(key) != 1:
            raise ValueError("Key must be a single character, such as q.")
        if edge_margin < 0 or poll_ms < 1 or cooldown_ms < 0:
            raise ValueError("Margin and cooldown cannot be negative; scan interval must be at least 1 ms.")
        return {"left": left, "top": top, "width": width, "height": height, "key": key,
                "edge_margin": edge_margin, "poll_ms": poll_ms, "cooldown_ms": cooldown_ms,
                "always_on_top": self.on_top.get()}

    def save_settings(self) -> None:
        try:
            self.config = self.read_ui_config()
            SETTINGS_DIR.mkdir(exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        except ValueError:
            # An incomplete edit will be validated when Apply or Start is clicked.
            pass

    def show_frame(self) -> None:
        try:
            self.save_settings()
            self.overlay.show()
            self.status.set("Drag the frame to move it; drag its bottom-right square to resize.")
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Invalid frame", str(exc))

    def apply_always_on_top(self) -> None:
        self.root.attributes("-topmost", self.on_top.get())
        self.save_settings()

    def start(self) -> None:
        try:
            config = self.read_ui_config()
        except ValueError as exc:
            messagebox.showerror("Cannot start", str(exc))
            return
        with self.config_lock:
            self.config = config
        self.save_settings()
        # Keep the overlay out of the captured pixels while the macro is active.
        self.overlay.hide()
        self.running.set()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Running - press F8 anywhere to stop.")
        self.log_debug("START: capture frame is hidden; scanning selected pixels only.")
        self.worker = threading.Thread(target=self.capture_loop, name="timing-macro", daemon=True)
        self.worker.start()

    def test_key(self) -> None:
        """Issue the selected key once on request, for input troubleshooting."""
        try:
            key = self.read_ui_config()["key"]
        except ValueError as exc:
            messagebox.showerror("Cannot test key", str(exc))
            return
        controller = keyboard.Controller()
        controller.press(key)
        controller.release(key)
        self.status.set(f"Manual test sent: {key.upper()}.")
        self.log_debug(f"MANUAL TEST: issued {key.upper()} keypress.")

    def save_capture(self) -> None:
        """Save precisely the screen pixels that the detector will inspect."""
        try:
            config = self.read_ui_config()
            region = {name: config[name] for name in ("left", "top", "width", "height")}
            output = APP_DIR / "debug_capture.png"
            with mss.mss() as capture:
                shot = capture.grab(region)
                mss_tools.to_png(shot.rgb, shot.size, output=str(output))
        except (ValueError, mss.exception.ScreenShotError, OSError) as exc:
            messagebox.showerror("Capture failed", str(exc))
            self.log_debug(f"CAPTURE ERROR: {exc}")
            return
        self.status.set("Saved the exact detection area to debug_capture.png.")
        self.log_debug("CAPTURE SAVED: debug_capture.png")

    def stop(self) -> None:
        if not self.running.is_set():
            return
        self.running.clear()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set("Stopped. You can show and adjust the capture frame.")
        self.log_debug("STOP: macro stopped.")

    def hotkey(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if key == keyboard.Key.f8:
            self.root.after(0, self.stop)

    def update_status(self, text: str) -> None:
        if self.running.is_set():
            self.root.after(0, lambda: self.status.set(text))

    def log_debug(self, message: str) -> None:
        """Queue a worker-safe diagnostic line; the GUI displays it shortly."""
        stamp = time.strftime("%H:%M:%S")
        try:
            self.debug_queue.put_nowait(f"[{stamp}] {message}")
        except queue.Full:
            try:
                self.debug_queue.get_nowait()
                self.debug_queue.put_nowait(f"[{stamp}] {message}")
            except queue.Empty:
                pass

    def flush_debug(self) -> None:
        """Move queued diagnostics into Tk on the main thread."""
        messages: list[str] = []
        while True:
            try:
                messages.append(self.debug_queue.get_nowait())
            except queue.Empty:
                break
        if messages:
            self.debug_output.configure(state="normal")
            self.debug_output.insert("end", "\n".join(messages) + "\n")
            # Retain the newest reports, keeping the debug box responsive.
            lines = int(self.debug_output.index("end-1c").split(".")[0])
            if lines > 150:
                self.debug_output.delete("1.0", f"{lines - 150}.0")
            self.debug_output.see("end")
            self.debug_output.configure(state="disabled")
        if self.root.winfo_exists():
            self.root.after(120, self.flush_debug)

    def clear_debug(self) -> None:
        self.debug_output.configure(state="normal")
        self.debug_output.delete("1.0", "end")
        self.debug_output.configure(state="disabled")

    def set_debug_summary(self, text: str) -> None:
        if self.running.is_set():
            self.root.after(0, lambda: self.debug_summary.set(text))

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
                    frame = np.asarray(capture.grab(region))[:, :, :3]  # BGR
                except mss.exception.ScreenShotError as exc:
                    self.update_status(f"Capture error: {exc}")
                    time.sleep(0.2)
                    continue
                rgb = frame[:, :, ::-1]
                found = find_target_and_marker(rgb)
                now = time.monotonic()
                if found is None:
                    armed = True
                    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
                    green_columns = int(np.count_nonzero(
                        (g > 115) & (g > r * 1.45) & (g > b * 1.25)
                    ))
                    white_columns = int(np.count_nonzero((r > 215) & (g > 215) & (b > 215)))
                    summary = (f"NOT DETECTED | green pixels: {green_columns} | "
                               f"white pixels: {white_columns}")
                    self.update_status("Running - looking for green target and white marker...")
                    if now - last_report >= 0.25:
                        self.set_debug_summary(summary)
                        self.log_debug(summary + " (frame may be too large/small or include borders)")
                        last_report = now
                else:
                    (left, right), marker_x, method = found
                    inside = left + config["edge_margin"] <= marker_x <= right - config["edge_margin"]
                    summary = (f"DETECTED | target: {left}-{right}px | marker: {marker_x}px | "
                               f"inside: {inside} | {method}")
                    if inside and armed and (now - last_press) * 1000 >= config["cooldown_ms"]:
                        controller.press(config["key"])
                        controller.release(config["key"])
                        armed, last_press = False, now
                        self.set_debug_summary(summary + f" | KEY SENT: {config['key'].upper()}")
                        self.log_debug(summary + f" | KEY SENT: {config['key'].upper()}")
                        self.update_status(
                            f"{config['key'].upper()} sent - marker {marker_x}px, target {left}-{right}px."
                        )
                    elif not inside:
                        armed = True
                        self.update_status(f"Running - marker {marker_x}px; target {left}-{right}px.")
                    if now - last_report >= 0.25:
                        self.set_debug_summary(summary)
                        self.log_debug(summary + (" | armed" if armed else " | waiting to re-arm"))
                        last_report = now
                time.sleep(config["poll_ms"] / 1000)

    def close(self) -> None:
        self.stop()
        self.listener.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    MacroApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
