"""Static checks for the boot artwork. Plymouth and GRUB cannot run in CI, so these catch the mistakes
that would only show up as a black or default boot screen: a missing image, a lost firmware logo, an
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


def plymouth_theme() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str  # Plymouth's keys are case-sensitive
    cp.read(PLY / "boppos.plymouth")
    return cp


def test_plymouth_is_a_two_step_theme_that_keeps_the_firmware_logo():
    # The firmware (ACPI BGRT) logo, ASRock or ASUS for example, can only be drawn by two-step: a script theme
    # loses it. Boot, shutdown and reboot all keep it.
    cp = plymouth_theme()
    assert cp["Plymouth Theme"]["ModuleName"] == "two-step"
    assert cp["two-step"]["ImageDir"] == "/usr/share/plymouth/themes/boppos"
    for mode in ("boot-up", "shutdown", "reboot"):
        assert cp[mode]["UseFirmwareBackground"] == "true", mode
        assert cp[mode]["UseEndAnimation"] == "false", mode
    assert cp["two-step"]["BackgroundStartColor"] == cp["two-step"]["BackgroundEndColor"] == "0x000000"
    # the wheel below the logo, the wordmark at the bottom
    assert float(cp["two-step"]["VerticalAlignment"]) > 0.5
    assert float(cp["two-step"]["WatermarkVerticalAlignment"]) > float(cp["two-step"]["VerticalAlignment"])


def test_plymouth_ships_every_image_two_step_needs():
    # Without entry/bullet/lock two-step does not start (no passphrase prompt on an encrypted disk); without an
    # animation-* file it takes the throbber frames as its end animation and shows no wheel.
    for name in ("entry.png", "bullet.png", "lock.png", "animation-0001.png", "bgrt-fallback.png", "watermark.png"):
        assert (PLY / name).exists(), name
    frames = sorted(PLY.glob("throbber-*.png"))
    assert [f.name for f in frames] == [f"throbber-{i:04d}.png" for i in range(1, len(frames) + 1)]
    assert len(frames) >= 30  # two-step plays them over 2 s at 30 frames a second
    assert len({png_info(f)[:2] for f in frames}) == 1  # one size, or the wheel jumps
    assert len({f.read_bytes() for f in frames}) == len(frames)  # the wheel actually turns
    assert not list(PLY.glob("*.script"))


def test_plymouth_art_is_rgba_and_the_watermark_keeps_the_logo_proportions():
    for path in PLY.glob("*.png"):
        assert png_info(path)[2] == 6, path.name
    w, h, _ = png_info(PLY / "watermark.png")
    svg = ET.parse(ART / "boppos-logo-horizontal-white.svg").getroot()
    vb = [float(v) for v in svg.get("viewBox").split()]
    assert abs(w / h - vb[2] / vb[3]) < 0.02


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
