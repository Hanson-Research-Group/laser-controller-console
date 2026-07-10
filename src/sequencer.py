#!/usr/bin/env python3
"""
UI-agnostic laser ramp / sequence engine for the Newport LDC-3908.

This is the safety-critical control logic — the per-channel safety state machine
and the temperature/current ramps — lifted out of the Tk GUI. It drives hardware
through a LaserController (self.ctl) and reports everything the UI needs to draw
through a SequenceEvents sink (self.events) instead of touching widgets directly.

No GUI imports. Exercised head-less in test_sequencer.py against the simulator.
"""

import math
import time
from dataclasses import dataclass
from typing import List


# --- Semantic status / LED kinds ----------------------------------------------
# The engine names *intent*, not color. The GUI (theme.py) maps these kinds to
# actual colors for the active light/dark theme, keeping this module UI-agnostic.
C_INIT = "init"
C_OK = "ok"
C_FAULT = "fault"
C_RAMP_T = "ramp_t"
C_RAMP_I = "ramp_i"
LED_RAMP = "ramp"
LED_OK = "ok"
LED_FAULT = "fault"


class SequenceEvents:
    """Sink for everything the sequencer wants the UI to reflect. Default no-ops;
    the GUI subclasses this (marshalling each call onto the Tk main thread), and
    tests subclass it to record calls."""

    def on_status(self, idx, text, kind):
        """Channel idx's status line should read `text`, styled by semantic
        `kind` ("init"/"ok"/"warn"/"fault"/"ramp_t"/"ramp_i"). The GUI maps the
        kind to a themed color."""

    def on_led(self, idx, kind):
        """Channel idx's LED should show semantic `kind` ("ramp"/"ok"/"fault")."""

    def on_live_output(self, idx, kind, state):
        """kind is 'TEC' or 'LAS'; state is 'ON' or 'OFF'."""

    def on_live_value(self, idx, kind, value):
        """kind is 'T' (degC) or 'I' (mA); value is a float live reading."""

    def on_tick(self):
        """Periodic hook during a ramp so the UI can refresh its ETA display."""

    def on_channel_halted(self, idx):
        """Channel idx aborted via a cooperative STOP/EMO (a 'HALT')."""

    def on_channel_fault(self, idx, message):
        """Channel idx aborted on a hardware/validation fault with `message`."""


@dataclass
class ChannelPlan:
    """A snapshot of one channel's requested action, taken from the UI before the
    run starts (the UI is locked during execution, so the snapshot stays valid)."""
    idx: int          # 0-based index into the UI channel list
    ch_num: int       # 1-based hardware channel number
    tec_cmd: str      # "ON" / "OFF"
    las_cmd: str      # "ON" / "OFF"
    t_target: float   # target temperature (degC); meaningful only if targets_valid
    i_target: float   # target current (mA); meaningful only if targets_valid
    targets_valid: bool


class Sequencer:
    def __init__(self, controller, events=None):
        self.ctl = controller
        self.events = events or SequenceEvents()

    # ----------------------------------------------------
    # TOP-LEVEL SEQUENCE LOOP
    # ----------------------------------------------------
    def run(self, plans: List[ChannelPlan], t_ramp, i_ramp, t_off_target):
        """Execute each channel plan in order. Aborts the whole run on the first
        fault/halt (setting the controller's stop flag). All UI feedback goes
        through self.events. Returns nothing — the caller inspects the controller
        flags (is_stop_requested / is_emo_requested) for final status, exactly as
        before."""
        for plan in plans:
            if self.ctl.is_stop_requested:
                break

            idx = plan.idx
            ch_num = plan.ch_num

            if not plan.targets_valid:
                self.events.on_status(idx, "Invalid targets", C_FAULT)
                continue

            tec_on_off = plan.tec_cmd
            las_on_off = plan.las_cmd
            t_on_target = plan.t_target
            i_on_target = plan.i_target

            try:
                # Lock serial during execution steps
                with self.ctl.serial_lock:
                    self.ctl.send_cmd(f"CHAN {ch_num}")
                    time.sleep(0.1)

                    # Read hardware limits from controller
                    h_i_lim_str = self.ctl.query_cmd("LAS:LIM:I?")
                    try:
                        h_i_lim = float(h_i_lim_str)
                    except Exception:
                        h_i_lim = 500.0

                    h_t_lim_str = self.ctl.query_cmd("TEC:LIM:THI?")
                    try:
                        h_t_lim = float(h_t_lim_str)
                    except Exception:
                        h_t_lim = 80.0

                    # Safety Validation Checks
                    if tec_on_off == "ON" and not math.isnan(h_t_lim) and t_on_target > h_t_lim:
                        raise ValueError(f"Target T ({t_on_target:.1f}°C) exceeds limit ({h_t_lim:.1f}°C)")

                    if las_on_off == "ON" and not math.isnan(h_i_lim) and i_on_target > h_i_lim:
                        raise ValueError(f"Target I ({i_on_target:.1f}mA) exceeds limit ({h_i_lim:.1f}mA)")

                    if tec_on_off == "OFF" and las_on_off == "ON":
                        raise ValueError("TEC must be ON for LAS to be ON.")

                    # Core execution logic
                    self.run_control_core(ch_num, tec_on_off, t_on_target, t_off_target,
                                          las_on_off, i_on_target, t_ramp, i_ramp)

                    # Final check for silent hardware error
                    has_err, err_str = self.ctl.check_controller_errors(ch_num)
                    if has_err:
                        raise RuntimeError(err_str)

            except Exception as e:
                err_msg = str(e)
                if "HALT" in err_msg:
                    self.events.on_channel_halted(idx)
                else:
                    self.events.on_channel_fault(idx, err_msg)

                self.ctl.is_stop_requested = True
                break

    # ----------------------------------------------------
    # PER-CHANNEL SAFETY STATE MACHINE
    # ----------------------------------------------------
    def run_control_core(self, ch_num, tec_on_off, t_on_target, t_off_target,
                         las_on_off, i_on_target, t_ramp, i_ramp):
        idx = ch_num - 1

        self.events.on_status(idx, "Initializing...", C_INIT)
        self.events.on_led(idx, LED_RAMP)

        # 1. Command Verification Alignment
        chan_curr = -1
        for retry in range(3):
            try:
                chan_curr = int(self.ctl.query_cmd("CHAN?"))
                if chan_curr == ch_num:
                    break
            except Exception:
                pass
            self.ctl.safe_pause(0.15)
            self.ctl.cmd_pause(f"CHAN {ch_num}")

        if chan_curr != ch_num:
            raise RuntimeError(f"Ch. switch to {ch_num} timed out or failed.")

        self.ctl.cmd_pause("LAS:MOD 0")
        self.ctl.safe_pause(0.1)
        self.ctl.verify_hw_state("LAS:MOD?", 0, "Hardware failed to disable external modulation.")

        # 2. Read Current Status
        try:
            tec_curr_status = int(float(self.ctl.query_cmd("TEC:OUT?")))
        except Exception:
            tec_curr_status = -1

        try:
            las_curr_status = int(float(self.ctl.query_cmd("LAS:OUT?")))
        except Exception:
            las_curr_status = -1

        # Safety Routing State Machine Matching MATLAB
        if tec_curr_status == 0 and las_curr_status == 0:
            if tec_on_off == "ON" and las_on_off == "OFF":
                self.tec_temp_tset_tcurr()
                self.ctl.safe_pause(0.15)
                self.ctl.send_cmd("TEC:OUTPUT 1")
                self.ctl.safe_pause(0.2)
                self.ctl.verify_hw_state("TEC:OUT?", 1, "TEC ON acknowledge failed.")
                self.events.on_live_output(idx, "TEC", "ON")
                self.ctl.safe_pause(0.15)
                self.ramp_temp(t_on_target, t_ramp, idx)

            elif tec_on_off == "ON" and las_on_off == "ON":
                self.tec_temp_tset_tcurr()
                self.ctl.safe_pause(0.15)
                self.ctl.send_cmd("TEC:OUTPUT 1")
                self.ctl.safe_pause(0.2)
                self.ctl.verify_hw_state("TEC:OUT?", 1, "TEC ON acknowledge failed.")
                self.events.on_live_output(idx, "TEC", "ON")
                self.ctl.safe_pause(0.15)
                self.ramp_temp(t_on_target, t_ramp, idx)

                self.ctl.safe_pause(0.15)
                self.ctl.send_cmd("LAS:LDI 0.0")
                self.ctl.safe_pause(0.15)
                self.ctl.send_cmd("LAS:OUTPUT 1")
                self.ctl.safe_pause(0.2)
                self.ctl.verify_hw_state("LAS:OUT?", 1, "LAS ON acknowledge failed.")
                self.events.on_live_output(idx, "LAS", "ON")
                self.ctl.safe_pause(2.5)  # Mandatory safety lock delay

                has_hw_err, hw_err_str = self.ctl.check_controller_errors(ch_num)
                if has_hw_err:
                    raise RuntimeError(hw_err_str)

                self.ramp_current(i_on_target, i_ramp, idx)

        elif tec_curr_status == 1 and las_curr_status == 0:
            if tec_on_off == "ON" and las_on_off == "ON":
                self.ramp_temp(t_on_target, t_ramp, idx)
                self.ctl.safe_pause(0.15)

                self.ctl.send_cmd("LAS:LDI 0.0")
                self.ctl.safe_pause(0.15)
                self.ctl.send_cmd("LAS:OUTPUT 1")
                self.ctl.safe_pause(0.2)
                self.ctl.verify_hw_state("LAS:OUT?", 1, "LAS ON acknowledge failed.")
                self.events.on_live_output(idx, "LAS", "ON")
                self.ctl.safe_pause(0.5)

                has_hw_err, hw_err_str = self.ctl.check_controller_errors(ch_num)
                if has_hw_err:
                    raise RuntimeError(hw_err_str)

                self.ramp_current(i_on_target, i_ramp, idx)

            elif tec_on_off == "OFF" and las_on_off == "OFF":
                self.ramp_temp(t_off_target, t_ramp, idx)
                self.ctl.safe_pause(0.15)
                self.ctl.send_cmd("TEC:OUTPUT 0")
                self.ctl.safe_pause(0.2)
                self.ctl.verify_hw_state("TEC:OUT?", 0, "TEC OFF acknowledge failed.")
                self.events.on_live_output(idx, "TEC", "OFF")
                self.ctl.safe_pause(0.5)

            elif tec_on_off == "ON" and las_on_off == "OFF":
                self.ramp_temp(t_on_target, t_ramp, idx)

        elif tec_curr_status == 1 and las_curr_status == 1:
            if tec_on_off == "ON" and las_on_off == "OFF":
                self.ramp_current(0.0, i_ramp, idx)
                self.ctl.safe_pause(0.15)
                self.ctl.send_cmd("LAS:OUTPUT 0")
                self.ctl.safe_pause(0.2)
                self.ctl.verify_hw_state("LAS:OUT?", 0, "LAS OFF acknowledge failed.")
                self.events.on_live_output(idx, "LAS", "OFF")
                self.ctl.safe_pause(0.5)
                self.ramp_temp(t_on_target, t_ramp, idx)

            elif tec_on_off == "OFF" and las_on_off == "OFF":
                self.ramp_current(0.0, i_ramp, idx)
                self.ctl.safe_pause(0.15)
                self.ctl.send_cmd("LAS:OUTPUT 0")
                self.ctl.safe_pause(0.2)
                self.ctl.verify_hw_state("LAS:OUT?", 0, "LAS OFF acknowledge failed.")
                self.events.on_live_output(idx, "LAS", "OFF")
                self.ctl.safe_pause(1.0)
                self.ramp_temp(t_off_target, t_ramp, idx)
                self.ctl.safe_pause(0.15)
                self.ctl.send_cmd("TEC:OUTPUT 0")
                self.ctl.safe_pause(0.2)
                self.ctl.verify_hw_state("TEC:OUT?", 0, "TEC OFF acknowledge failed.")
                self.events.on_live_output(idx, "TEC", "OFF")
                self.ctl.safe_pause(0.5)

            elif tec_on_off == "ON" and las_on_off == "ON":
                self.ramp_temp(t_on_target, t_ramp, idx)
                self.ctl.safe_pause(0.15)
                self.ramp_current(i_on_target, i_ramp, idx)
        else:
            if tec_curr_status == 0 and las_curr_status == 1:
                self.events.on_status(idx, "CRITICAL: Laser ON without TEC. Ramping down safely.", C_FAULT)
                self.ramp_current(0.0, i_ramp, idx)
                self.ctl.safe_pause(0.15)
                self.ctl.send_cmd("LAS:OUTPUT 0")
                self.ctl.safe_pause(0.2)
                self.ctl.verify_hw_state("LAS:OUT?", 0, "LAS OFF acknowledge failed.")
                self.events.on_live_output(idx, "LAS", "OFF")
                raise RuntimeError(f"CRITICAL FAULT CH {ch_num}: Laser ON while TEC OFF. Ramped down laser safely.")
            else:
                # Reached only when TEC/LAS output status could not be determined
                # (a query returned -1 / unparseable, or a value outside {0,1}).
                # Fail loud rather than silently skipping the channel: for laser
                # safety we must not proceed with an unknown output state.
                raise RuntimeError(
                    f"Could not read TEC/LAS output state for Ch {ch_num} "
                    f"(TEC={tec_curr_status}, LAS={las_curr_status}). Aborting for safety.")

        if not self.ctl.is_stop_requested:
            self.final_check(ch_num)

    def tec_temp_tset_tcurr(self):
        t_curr_str = self.ctl.query_cmd("TEC:T?")
        try:
            t_curr = float(t_curr_str)
            if not math.isnan(t_curr):
                self.ctl.cmd_pause(f"TEC:T {t_curr:.2f}")
        except Exception:
            pass

    # ----------------------------------------------------
    # RAMPS
    # ----------------------------------------------------
    def ramp_temp(self, t_target, t_ramp, idx):
        t_curr = None
        for retry in range(5):
            t_curr_str = self.ctl.query_cmd("TEC:SYNCT?")
            try:
                t_curr = float(t_curr_str)
                break
            except Exception:
                self.ctl.safe_pause(0.15)

        if t_curr is None or math.isnan(t_curr):
            raise RuntimeError("Telemetry lost during initial Thermal readout.")

        if abs(t_curr - t_target) < 0.05:
            self.events.on_status(idx, f"T at Target ({t_curr:.1f} °C)", C_OK)
            return

        t_set = t_curr
        t_start = t_curr
        # Time-based stepping: advance the setpoint by (rate * actual elapsed time)
        # each iteration instead of a fixed 0.5 s-worth of movement. The old code
        # added abs(t_ramp)*0.5 per loop while assuming every loop took exactly
        # 0.5 s, but each iteration also spends ~0.2-0.3 s inside query_cmd, so the
        # true ramp ran ~30% slower than the requested °C/s (and the ETA was
        # correspondingly optimistic). Measuring real elapsed time makes the
        # physical ramp rate — and the ETA — match the setting.
        direction = 1 if t_target > t_curr else -1
        NOMINAL_PERIOD = 0.5  # priming value so the first step isn't zero-length
        last_tick = time.time() - NOMINAL_PERIOD

        t_fail_count = 0
        while abs(t_set - t_target) > 0.01:
            if self.ctl.is_stop_requested:
                raise RuntimeError("HALT")

            now = time.time()
            dt = now - last_tick
            last_tick = now
            # Clamp dt so a stalled serial read (up to the port timeout) can't make
            # the setpoint jump by a large, unsafe increment in a single step.
            dt = max(0.0, min(dt, 1.0))

            t_set += direction * abs(t_ramp) * dt
            if (direction > 0 and t_set > t_target) or (direction < 0 and t_set < t_target):
                t_set = t_target

            self.ctl.send_cmd(f"TEC:T {t_set:.2f}")

            # Safe high-responsiveness loop pause (total 0.5s)
            p_start = time.time()
            while time.time() - p_start < 0.5:
                self.events.on_tick()
                self.ctl.safe_pause(0.1)

            t_curr_str = self.ctl.query_cmd("TEC:SYNCT?")
            try:
                t_curr = float(t_curr_str)
                if math.isnan(t_curr):
                    raise ValueError("NaN")
                t_fail_count = 0
            except Exception:
                t_fail_count += 1
                if t_fail_count > 3:
                    raise RuntimeError("Hardware communication lost during ramp. Stopping execution.")
                t_curr = t_set

            # Update entry box readout
            self.events.on_live_value(idx, "T", t_curr)

            # Draw progress bar text in status field
            pct = min(1.0, max(0.0, abs(t_curr - t_start) / max(0.01, abs(t_target - t_start))))
            num_blocks = round(pct * 10)
            prog_bar = '█' * num_blocks + '░' * (10 - num_blocks)

            self.events.on_status(idx, f"[{prog_bar}] ({t_curr:.1f} °C)", C_RAMP_T)

    def ramp_current(self, i_target, i_ramp, idx):
        i_curr = None
        for retry in range(5):
            i_curr_str = self.ctl.query_cmd("LAS:SYNCLDI?")
            try:
                i_curr = float(i_curr_str)
                break
            except Exception:
                self.ctl.safe_pause(0.15)

        if i_curr is None or math.isnan(i_curr):
            raise RuntimeError("Telemetry lost during initial Laser readout.")

        if abs(i_curr - i_target) < 0.05:
            self.events.on_status(idx, f"I at Target ({i_curr:.1f} mA)", C_OK)
            return

        i_set = i_curr
        i_start = i_curr
        # Time-based stepping (see ramp_temp for the full rationale): advance the
        # current setpoint by (rate * actual elapsed time) so the physical mA/s
        # ramp rate and the ETA match the requested value instead of running slow.
        direction = 1 if i_target > i_curr else -1
        NOMINAL_PERIOD = 0.5  # priming value so the first step isn't zero-length
        last_tick = time.time() - NOMINAL_PERIOD

        i_fail_count = 0
        while abs(i_set - i_target) > 0.01:
            if self.ctl.is_stop_requested:
                raise RuntimeError("HALT")

            now = time.time()
            dt = now - last_tick
            last_tick = now
            # Clamp dt so a stalled serial read (up to the port timeout) can't make
            # the setpoint jump by a large, unsafe increment in a single step.
            dt = max(0.0, min(dt, 1.0))

            i_set += direction * abs(i_ramp) * dt
            if (direction > 0 and i_set > i_target) or (direction < 0 and i_set < i_target):
                i_set = i_target

            self.ctl.send_cmd(f"LAS:LDI {i_set:.2f}")

            p_start = time.time()
            while time.time() - p_start < 0.5:
                self.events.on_tick()
                self.ctl.safe_pause(0.1)

            i_curr_str = self.ctl.query_cmd("LAS:SYNCLDI?")
            try:
                i_curr = float(i_curr_str)
                if math.isnan(i_curr):
                    raise ValueError("NaN")
                i_fail_count = 0
            except Exception:
                i_fail_count += 1
                if i_fail_count > 3:
                    raise RuntimeError("Hardware communication lost during ramp. Stopping execution.")
                i_curr = i_set

            self.events.on_live_value(idx, "I", i_curr)

            # Draw progress bar text in status field
            pct = min(1.0, max(0.0, abs(i_curr - i_start) / max(0.01, abs(i_target - i_start))))
            num_blocks = round(pct * 10)
            prog_bar = '█' * num_blocks + '░' * (10 - num_blocks)

            self.events.on_status(idx, f"[{prog_bar}] ({i_curr:.1f} mA)", C_RAMP_I)

    def final_check(self, ch_num):
        idx = ch_num - 1

        try:
            tec_stat = int(float(self.ctl.query_cmd("TEC:OUT?")))
        except Exception:
            tec_stat = -1

        try:
            las_stat = int(float(self.ctl.query_cmd("LAS:OUT?")))
        except Exception:
            las_stat = -1

        status_str = "Final Set: "
        if tec_stat == 1:
            status_str += "TEC ON, "
            self.events.on_live_output(idx, "TEC", "ON")
        else:
            status_str += "TEC OFF, "
            self.events.on_live_output(idx, "TEC", "OFF")

        if las_stat == 1:
            status_str += "LAS ON"
            self.events.on_live_output(idx, "LAS", "ON")
        else:
            status_str += "LAS OFF"
            self.events.on_live_output(idx, "LAS", "OFF")

        self.events.on_status(idx, status_str, C_OK)
        self.events.on_led(idx, LED_OK)

        self.ctl.cmd_pause("LAS:MOD 1")
