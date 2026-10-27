"""Static checks for the boot artwork. Plymouth and GRUB cannot run in CI, so these catch the mistakes
that would only show up as a black or default boot screen: a missing image, an undefined callback, an
unbalanced brace, a logo that quietly turned grey."""

import configparser
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parent.parent
PLY = ROOT / "boot/plymouth/boppos"
GRUB = ROOT / "boot/grub/boppos"
ART = ROOT / "artwork/icons/hicolor/scalable/apps"


def png_info(path: Path) -> tuple[int, int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    w, h = struct.unpack(">II", data[16:24])
    return w, h, data[25]  # colour type 6 = RGBA


def balanced(text: str, pairs=("{}", "()", "[]")) -> bool:
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)          # ignore what is inside strings
    text = re.sub(r"#.*", "", text)                           # and comments
    return all(text.count(a) == text.count(b) for a, b in pairs)


# --- Plymouth ------------------------------------------------------------------------------------


def test_plymouth_descriptor_points_at_files_that_ship():
    cp = configparser.ConfigParser()
    cp.read(PLY / "boppos.plymouth")
    assert cp["Plymouth Theme"]["ModuleName"] == "script"
    assert cp["script"]["ImageDir"] == "/usr/share/plymouth/themes/boppos"
    assert cp["script"]["ScriptFile"].endswith("/boppos/boppos.script")
    assert (PLY / Path(cp["script"]["ScriptFile"]).name).exists()


def test_plymouth_script_is_well_formed_and_only_uses_images_that_exist():
    script = (PLY / "boppos.script").read_text()
    assert balanced(script)
    images = set(re.findall(r'Image\("([^"]+)"\)', script))
    assert images == {"mark.png", "lockup.png"}
    assert all((PLY / name).exists() for name in images)
    assert "[]" not in script  # Plymouth's script language has no array literal


def test_every_plymouth_callback_that_is_registered_is_defined():
    script = (PLY / "boppos.script").read_text()
    defined = set(re.findall(r"^fun (\w+)\s*\(", script, re.M))
    registered = re.findall(r"Plymouth\.Set\w+Function\((\w+)\)", script)
    assert {"display_password_callback", "display_normal_callback", "refresh_callback"} <= set(registered)
    assert set(registered) <= defined, set(registered) - defined


def test_plymouth_background_is_black_and_the_passphrase_prompt_exists():
    script = (PLY / "boppos.script").read_text()
    assert "SetBackgroundTopColor(0, 0, 0)" in script and "SetBackgroundBottomColor(0, 0, 0)" in script
    assert "SetDisplayPasswordFunction" in script  # encrypted disks must be able to ask for the passphrase
    assert "SetDisplayQuestionFunction" in script


def test_plymouth_art_is_rgba_and_the_lockup_keeps_the_logo_proportions():
    for name in ("mark.png", "lockup.png"):
        assert png_info(PLY / name)[2] == 6
    w, h, _ = png_info(PLY / "lockup.png")
    svg = ET.parse(ART / "boppos-logo-horizontal-white.svg").getroot()
    vb = [float(v) for v in svg.get("viewBox").split()]
    assert abs(w / h - vb[2] / vb[3]) < 0.01


# --- GRUB ----------------------------------------------------------------------------------------


def test_grub_theme_is_well_formed_and_its_images_exist_at_the_declared_size():
    theme = (GRUB / "theme.txt").read_text()
    assert balanced(theme, ("{}",))
    assert 'desktop-color: "#000000"' in theme
    blocks = re.findall(r"\+ image \{(.*?)\}", theme, re.S)
    assert len(blocks) == 2
    for block in blocks:
        name = re.search(r'file = "([^"]+)"', block).group(1)
        w, h, _ = png_info(GRUB / name)
        assert int(re.search(r"width = (\d+)", block).group(1)) == w
        assert int(re.search(r"height = (\d+)", block).group(1)) == h
    assert 'id = "__timeout__"' in theme and "boot_menu" in theme


# --- the logos -----------------------------------------------------------------------------------


def test_logo_svgs_are_vector_only_single_colour_and_free_of_editor_junk():
    expected = {"white": "#ffffff", "black": "#16161a"}
    for layout in ("horizontal", "stacked"):
        for tone, colour in expected.items():
            path = ART / f"boppos-logo-{layout}-{tone}.svg"
            text = path.read_text()
            root = ET.fromstring(text)
            assert root.get("viewBox")
            assert "<text" not in text and "font" not in text.lower()  # the wordmark is paths, not a font
            assert "sodipodi" not in text and "krita" not in text.lower()
            assert set(re.findall(r'stroke="(#[0-9a-fA-F]{6})"', text)) == {colour}, path.name
            assert 'fill="none"' in text and "fill=\"#" not in text  # line art only


def test_the_dark_logo_for_light_panels_has_the_same_geometry_as_the_white_one():
    for layout in ("horizontal", "stacked"):
        white = (ART / f"boppos-logo-{layout}-white.svg").read_text().replace("#ffffff", "X")
        black = (ART / f"boppos-logo-{layout}-black.svg").read_text().replace("#16161a", "X")
        assert white == black
