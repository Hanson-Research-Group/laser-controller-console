#!/usr/bin/env python3
"""
Headless (offscreen) smoke test for the PySide6/Qt GUI (main.py).

Runs the real Qt event loop against a MULTI-controller Demo setup: an ILX
LDC-3908 (8 ch) plus a Wavelength TC10 + QCL1000 pairing (one split laser line),
both simulated. Exercises: build the system from a config, connect -> scan ->
configure a channel in each box -> run, and checks the simulator state, the
per-unit boxes, and the auto-hide visibility filter. Skips if PySide6 is absent.

Run:  python src/test_gui.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QTimer
except Exception as e:  # pragma: no cover
    print(f"SKIP: PySide6 not available ({e})")
    raise SystemExit(0)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


TWO_UNIT_CONFIG = {"units": [
    {"id": "u1", "kind": "combined", "driver": "ldc3908",
     "title": "ILX LDC-3908", "transport": {"type": "sim"}},
    {"id": "u2", "kind": "pairing", "title": "Wavelength Laser A",
     "temp": {"driver": "wavelength_tc", "transport": {"type": "sim"}},
     "current": {"driver": "wavelength_qcl", "transport": {"type": "sim"}}},
]}


def run():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    # Auto-dismiss modal dialogs so the loop never blocks.
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)

    import main
    w = main.LDCMainWindow()
    w.show()
    res = {}

    # Global indices: ILX channels are 0..7 (installed 0,1,4); the Wavelength
    # pair is the 9th binding, global idx 8.
    PAIR = 8

    def start():
        # Configure two controllers (ILX + a Wavelength T+I pairing), both Demo.
        # Done here (not before exec) so it wins over the startup profile autoload.
        w.config = TWO_UNIT_CONFIG
        w._rebuild_system()
        # Both view modes must build without error.
        w._set_view_mode(False)   # Cards
        w._set_view_mode(True)    # Table
        # Fast ramps so the smoke test finishes quickly.
        w.t_ramp.setText("50"); w.i_ramp.setText("50")
        w.connect_serial()   # connects all controllers and auto-scans each
        QTimer.singleShot(200, wait_scan)

    def wait_scan():
        # Auto-scan runs per controller on connect; wait until all are scanned.
        if not all(w._unit_scanned.get(u.id) for u in w.system.units):
            QTimer.singleShot(200, wait_scan)
            return
        res['shown'] = w._shown()
        res['boxes'] = len(w.unit_boxes)
        for i in (0, PAIR):
            c = w.cards[i]
            c.tec_cmd.setCurrentText("ON")
            c.las_cmd.setCurrentText("ON")
            c.t_target.setText("30.0" if i == PAIR else "23.0")
            c.i_target.setText("5.0" if i == PAIR else "6.0")
        w.execute_channels([0, PAIR])
        QTimer.singleShot(200, wait_run)

    def wait_run():
        if w.is_executing:
            QTimer.singleShot(200, wait_run)
            return
        ilx = w.system.channels[0].temp_driver
        res['ilx'] = (ilx.sim_state['TEC_ON'][0], ilx.sim_state['LAS_ON'][0],
                      round(ilx.sim_state['T_actual'][0], 2), round(ilx.sim_state['I_actual'][0], 2))
        b = w.system.channels[PAIR]
        tc, qcl = b.temp_driver, b.current_driver
        res['pair'] = (tc.sim_state['TEC_ON'], qcl.sim_state['LAS_ON'],
                       round(tc.sim_state['T_actual'], 2), round(qcl.sim_state['I_actual'], 2))
        res['status0'] = w.cards[0].status.text()
        res['status_pair'] = w.cards[PAIR].status.text()
        app.quit()

    QTimer.singleShot(200, start)
    QTimer.singleShot(60000, app.quit)  # safety timeout
    app.exec()
    return res


def config_edit_checks():
    """Synchronous checks of the per-controller rename / reorder / drag-reorder /
    delete operations (dialogs stubbed). No event loop needed."""
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
    from PySide6.QtCore import QPoint
    app = QApplication.instance() or QApplication(sys.argv)
    QInputDialog.getText = staticmethod(lambda *a, **k: ("Renamed", True))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.information = QMessageBox.warning = staticmethod(lambda *a, **k: None)

    import main
    # Patch at class level so the startup autoload (bound in __init__) is a no-op.
    main.LDCMainWindow._load_last_profile = lambda self: None
    w = main.LDCMainWindow()
    w.config = {"units": [
        {"id": "u1", "kind": "combined", "driver": "ldc3908", "title": "ILX", "transport": {"type": "sim"}},
        {"id": "u2", "kind": "combined", "driver": "thorlabs_itc", "title": "Thor", "transport": {"type": "sim"}},
        {"id": "u3", "kind": "pairing", "title": "WL",
         "temp": {"driver": "wavelength_tc", "transport": {"type": "sim"}},
         "current": {"driver": "wavelength_qcl", "transport": {"type": "sim"}}},
    ]}
    w._rebuild_system(); w.resize(1400, 800); w.show(); app.processEvents()
    titles = lambda: [u["title"] for u in w.config["units"]]
    out = []

    # A non-ILX (single-channel) controller stays active even at a negative
    # temperature (which can legitimately happen) — no "no laser" rejection.
    thor = [u for u in w.system.units if u.id == "u2"][0]
    td = thor.devices[0]; td.open_simulator()
    td.sim_state["T_actual"] = -12.5; td.sim_state["T_set"] = -12.5
    b = thor.channels[0]
    populated_ok = w._scan_binding(b)
    app.processEvents()
    out.append(("non-ILX stays active at negative temp",
                populated_ok and w.populated[b.idx]
                and abs(float(w.cards[b.idx].live_t.text()) + 12.5) < 0.1))
    td.close()

    w.cards[2].t_target.setText("37.7"); w.cards[2].label.setText("Keep")  # ILX ch3
    w._rename_unit("u2")
    out.append(("rename controller", titles()[1] == "Renamed"))
    w._move_unit("u3", -1)
    out.append(("move controller up", titles() == ["ILX", "WL", "Renamed"]))
    w._reorder_units("u1", "u2", False)  # ILX after the (renamed) thorlabs
    out.append(("drag-reorder controllers", titles()[-1] == "ILX"))

    ilx = [u for u in w.system.units if u.id == "u1"][0]
    src = [b for b in ilx.channels if b.ch_num == 5][0]
    gp = w.cards[ilx.channels[0].idx].mapToGlobal(QPoint(5, 2))
    w._drop_channel(src.idx, "u1", gp)
    order = [b.ch_num for b in [u for u in w.system.units if u.id == "u1"][0].channels]
    out.append(("drag-reorder channels (ch5 to front)", order[0] == 5))
    ch3 = [b for b in [u for u in w.system.units if u.id == "u1"][0].channels if b.ch_num == 3][0]
    out.append(("targets preserved across reorder", w.cards[ch3.idx].t_target.text() == "37.7"))
    w._delete_unit("u2")
    out.append(("delete controller", "Renamed" not in titles()))
    w._unsaved = False  # avoid the close-time "save changes?" prompt (unstubbed file dialog)
    w.close()
    return out


def main():
    # Subprocess entry: run only the config-edit checks in a clean QApplication.
    if "--config-edits" in sys.argv:
        out = config_edit_checks()
        for name, passed in out:
            print(f"  {'PASS' if passed else 'FAIL'}  {name}")
        raise SystemExit(0 if all(p for _, p in out) else 1)

    res = run()
    print("shown channels after scan:", res.get('shown'), " unit boxes:", res.get('boxes'))
    print("ILX ch1 (TEC,LAS,T,I):", res.get('ilx'))
    print("Wavelength pair (TEC,LAS,T,I):", res.get('pair'))
    print("statuses:", res.get('status0'), "|", res.get('status_pair'))

    ilx = res.get('ilx') or (0, 0, 0, 0)
    pair = res.get('pair') or (0, 0, 0, 0)
    checks = [
        ("two unit boxes built", res.get('boxes') == 2),
        ("scan showed ILX installed + the pairing (idx 0,1,4,8)", res.get('shown') == [0, 1, 4, 8]),
        ("ILX ch1 reached TEC+LAS ON at 23/6", ilx[0] == 1 and ilx[1] == 1
         and abs(ilx[2] - 23.0) < 0.1 and abs(ilx[3] - 6.0) < 0.1),
        ("Wavelength pair reached TEC+LAS ON at 30/5 across two devices",
         pair[0] == 1 and pair[1] == 1 and abs(pair[2] - 30.0) < 0.1 and abs(pair[3] - 5.0) < 0.1),
        ("final statuses set", str(res.get('status0', "")).startswith("Final Set:")
         and str(res.get('status_pair', "")).startswith("Final Set:")),
    ]
    ok = True
    for name, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
        ok = ok and passed

    # Controller rename/reorder/delete ops run in a clean subprocess (a second
    # QApplication in-process deadlocks on the first window's lingering timers).
    import subprocess
    print("-- controller edit ops --")
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--config-edits"],
                       capture_output=True, text=True, env=dict(os.environ))
    print((r.stdout or "").rstrip())
    if r.returncode != 0 and (r.stderr or "").strip():
        print(r.stderr.rstrip())
    ok = ok and (r.returncode == 0)

    print("\nQt offscreen smoke test:", "PASSED" if ok else "FAILED")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
