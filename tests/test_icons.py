"""The BoppOS icon is referenced by name in several places; make sure those names exist and agree."""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
ICON = "boppos-icon"


def test_the_icon_exists_in_every_size_we_ship():
    sizes = {p.parent.parent.name for p in (ROOT / "artwork/icons/hicolor").glob("*/apps/boppos-icon.png")}
    assert {"16x16", "32x32", "48x48", "64x64", "128x128", "256x256", "512x512"} <= sizes


def test_desktop_files_use_the_shipped_icon():
    for path in [*(ROOT / "share/applications").glob("*.desktop"), *(ROOT / "autostart").glob("*.desktop")]:
        assert f"Icon={ICON}" in path.read_text(), path.name


def test_the_app_desktop_file_is_named_after_the_application_id():
    """On Wayland the window is matched to its desktop file (and icon) by application id."""
    app = (ROOT / "lib/boppos/py/boppos/ui/app.py").read_text()
    app_id = re.search(r'^APP_ID = "([^"]+)"', app, re.M).group(1)
    assert (ROOT / f"share/applications/{app_id}.desktop").exists()
    assert f'ICON_NAME = "{ICON}"' in app and "set_default_icon_name(ICON_NAME)" in app


def test_notifications_and_polkit_prompts_carry_the_icon():
    tray = (ROOT / "bin/bopp-tray").read_text()
    assert tray.count(f"--icon={ICON}") == 2 and "--icon=system-" not in tray
    policy = (ROOT / "polkit/actions/org.boppos.deck.policy").read_text()
    assert policy.count("<action ") == policy.count(f"<icon_name>{ICON}</icon_name>") >= 3


def test_blue_logo_backdrop_covers_the_whole_canvas():
    # the backdrop must be a direct child of the root: inside the stacked logo's centring group it was shifted
    # sideways and left a transparent strip
    import xml.etree.ElementTree as ET

    root = Path(__file__).parent.parent / "artwork/icons/hicolor/scalable/apps"
    for kind in ("horizontal", "stacked"):
        svg = ET.parse(root / f"boppos-logo-{kind}-blue.svg").getroot()
        x, y, w, h = (float(v) for v in svg.get("viewBox").split())
        rects = [c for c in svg if c.tag.endswith("}rect")]
        assert len(rects) == 1, kind
        r = rects[0]
        got = tuple(float(r.get(k)) for k in ("x", "y", "width", "height"))
        assert got == (x, y, w, h), kind
