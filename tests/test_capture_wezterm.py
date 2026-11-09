"""The macOS WezTerm capture script's platform-independent checks."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("capture", ROOT / "scripts/capture_wezterm_macos.py")
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)
LINE = ("    { key = 'A', mods = 'CTRL', action = act.SpawnCommandInNewTab{ args = "
        "{ '/opt/awb/bin/awb', '--home', '/state', 'tui' }, domain = 'CurrentPaneDomain' } },")


def test_shortcut_is_found_in_wezterm_normalized_form():
    # `wezterm show-keys --lua` prints Ctrl+Shift+A as key 'A' with mods 'CTRL'.
    assert capture.shortcut_bound("other = 1\n" + LINE)
    assert capture.shortcut_bound(LINE.replace("key = 'A', mods = 'CTRL'", "key = 'a', mods = 'SHIFT|CTRL'"))
    # Current installs bind `awb open`; older ones bound `awb tui`.
    assert capture.shortcut_bound(LINE.replace("'tui'", "'open'"))


def test_other_bindings_are_not_mistaken_for_the_shortcut():
    assert not capture.shortcut_bound(LINE.replace("key = 'A', mods = 'CTRL'", "key = 'a', mods = 'CTRL'"))
    assert not capture.shortcut_bound(LINE.replace("'tui'", "'logs'"))
    assert not capture.shortcut_bound("")
