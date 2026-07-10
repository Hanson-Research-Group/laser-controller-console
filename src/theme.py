#!/usr/bin/env python3
"""
Centralized theme tokens for the LDC-3908 GUI.

Two ideas keep the app themeable and the control engine UI-agnostic:

  * Semantic *status kinds*. The sequence engine (sequencer.py) never names a
    color — it emits a kind like "ok" / "fault" / "ramp_t". The GUI maps that
    kind to an actual color here, so the same run looks right in either theme.

  * (light, dark) tuples. CustomTkinter auto-switches a widget's color by the
    active appearance mode when you give it a 2-tuple, so status/label text
    defined here flips automatically on toggle. The LED indicator is drawn on a
    bare tk.Canvas that CTk does NOT theme, so its hues are single values chosen
    to read on both light and dark frames, and its background is resolved live.
"""

import customtkinter as ctk

# --- Status text colors (channel status line + main status label). ---
# CTk auto-switches these (light, dark) tuples on appearance-mode change.
STATUS = {
    "init":   ("#333333", "#e0e0e0"),
    "ok":     ("#2e7d32", "#66bb6a"),
    "warn":   ("#e65100", "#ffa726"),
    "fault":  ("#c62828", "#ef5350"),
    "ramp_t": ("#1565c0", "#64b5f6"),
    "ramp_i": ("#6a1b9a", "#ce93d8"),
    "muted":  ("#757575", "#9e9e9e"),
    "demo":   ("#7b1fa2", "#ce93d8"),
    "accent": ("#1565c0", "#64b5f6"),
}

# --- LED indicator hues (single value; drawn on a bare canvas). ---
LED = {
    "idle":  "#b0bec5",
    "empty": "#78909c",
    "ok":    "#43a047",
    "warn":  "#fb8c00",
    "fault": "#e53935",
    "ramp":  "#fdd835",
}

# --- Buttons / accents (brand hues, fine on both modes). ---
ACCENT = "#1976D2"
ACCENT_HOVER = "#1565C0"
GREEN = "#2e7d32"
GREEN_HOVER = "#1b5e20"
RED = "#c62828"
RED_HOVER = "#b71c1c"

# --- Live readout box (disabled CTkEntry) states. ---
LIVE_IDLE_BG = ("#e0e0e0", "#3a3a3a")
LIVE_IDLE_TEXT = ("black", "#b0b0b0")
LIVE_ON_BG = ("#2e7d32", "#2e7d32")
LIVE_ON_TEXT = ("white", "white")


def status(kind):
    """Map a semantic status kind to a (light, dark) text-color tuple."""
    return STATUS.get(kind, STATUS["muted"])


def led(kind):
    """Map a semantic LED kind to a single hue that reads on both themes."""
    return LED.get(kind, LED["idle"])


def frame_bg():
    """Best-effort background color of a default CTkFrame for the active mode.

    Used by the custom LED canvas (which CTk cannot theme) so its background
    matches the panel behind it in both light and dark modes."""
    idx = 0 if ctk.get_appearance_mode().lower() == "light" else 1
    try:
        fg = ctk.ThemeManager.theme["CTkFrame"]["fg_color"]
        return fg[idx] if isinstance(fg, (list, tuple)) else fg
    except Exception:
        return "#ebebeb" if idx == 0 else "#2b2b2b"
