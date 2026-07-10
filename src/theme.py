#!/usr/bin/env python3
"""
Centralized theme tokens for the Laser Controller Console GUI.

Semantic *status kinds* keep the control engine UI-agnostic: the sequencer
(sequencer.py) never names a color — it emits a kind like "ok" / "fault" /
"ramp_t", and the GUI maps that kind to an actual color here, so the same run
looks right in either light or dark theme.

Each entry is a (light, dark) pair; the GUI picks the side matching the active
theme (see main.status_hex). No GUI-toolkit dependency here.
"""

# --- Status text colors (channel status line + main status label). ---
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

# --- LED indicator hues (single value; reads on both themes). ---
LED = {
    "idle":  "#b0bec5",
    "empty": "#78909c",
    "ok":    "#43a047",
    "warn":  "#fb8c00",
    "fault": "#e53935",
    "ramp":  "#fdd835",
}


def status(kind):
    """Map a semantic status kind to a (light, dark) text-color tuple."""
    return STATUS.get(kind, STATUS["muted"])


def led(kind):
    """Map a semantic LED kind to a single hue that reads on both themes."""
    return LED.get(kind, LED["idle"])
