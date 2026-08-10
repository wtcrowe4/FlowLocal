"""Hotkey name matching must survive case differences between config and events.

The `keyboard` library reports F-key events lowercase ('f24'), while configs
commonly spell them 'F24'. add_hotkey() normalized that for us; the raw hooks
compare event names directly, so without explicit normalization the toggle
silently never fires.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import keyboard
import app as flowlocal


def _install_and_capture(monkeypatch_cfg):
    """Install the key hooks with a given CFG and return the captured callbacks."""
    hooks = []
    real_hook, real_unhook = keyboard.hook, keyboard.unhook_all
    keyboard.hook = lambda cb: hooks.append(cb)
    keyboard.unhook_all = lambda: None
    saved = dict(flowlocal.CFG)
    try:
        flowlocal.CFG.update(monkeypatch_cfg)
        flowlocal._install_key_hooks()
    finally:
        keyboard.hook, keyboard.unhook_all = real_hook, real_unhook
        flowlocal.CFG.clear()
        flowlocal.CFG.update(saved)
    return hooks


def _fire(hooks, name, scan_code, event_type="down"):
    e = keyboard.KeyboardEvent(event_type=event_type, scan_code=scan_code, name=name)
    for cb in hooks:
        cb(e)


def test_toggle_fires_for_lowercase_event_name_with_uppercase_config():
    """Config 'F24' must match the real event, which arrives as 'f24'."""
    fired = []
    real_toggle = flowlocal._on_toggle
    flowlocal._on_toggle = lambda: fired.append(True)
    try:
        hooks = _install_and_capture({"toggle_hotkey": "F24",
                                      "hold_hotkey": "", "ask_hotkey": ""})
        # scan 118 / name 'f24' is exactly what SendInput-injected F24 produces
        _fire(hooks, "f24", 118)
    finally:
        flowlocal._on_toggle = real_toggle
    assert fired, "toggle did not fire for event name 'f24' with config 'F24'"


def test_toggle_still_fires_when_config_matches_case():
    fired = []
    real_toggle = flowlocal._on_toggle
    flowlocal._on_toggle = lambda: fired.append(True)
    try:
        hooks = _install_and_capture({"toggle_hotkey": "f24",
                                      "hold_hotkey": "", "ask_hotkey": ""})
        _fire(hooks, "f24", 118)
    finally:
        flowlocal._on_toggle = real_toggle
    assert fired, "toggle did not fire for the already-lowercase config"


def test_toggle_ignores_a_different_key():
    fired = []
    real_toggle = flowlocal._on_toggle
    flowlocal._on_toggle = lambda: fired.append(True)
    try:
        hooks = _install_and_capture({"toggle_hotkey": "F24",
                                      "hold_hotkey": "", "ask_hotkey": ""})
        _fire(hooks, "a", 30)
    finally:
        flowlocal._on_toggle = real_toggle
    assert not fired, "toggle fired for an unrelated key"


def _main():
    """Run without pytest - the venv has no test runner installed."""
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


def test_hold_matches_case_insensitively():
    presses = []
    real_press = flowlocal._on_hold_press
    flowlocal._on_hold_press = lambda e: presses.append(True)
    try:
        hooks = _install_and_capture({"hold_hotkey": "Right Ctrl",
                                      "toggle_hotkey": "", "ask_hotkey": ""})
        _fire(hooks, "right ctrl", 29)
    finally:
        flowlocal._on_hold_press = real_press
    assert presses, "hold did not fire for 'right ctrl' with config 'Right Ctrl'"


if __name__ == "__main__":
    raise SystemExit(_main())
