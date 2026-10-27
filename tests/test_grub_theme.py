"""bopp-grub-theme against a fake boot partition built from the real Fedora-style grub.cfg of a BoppOS machine
(tests/fixtures/fedora-grub.cfg). Nothing here touches a real /boot."""

import importlib.machinery
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "boot/grub/bopp-grub-theme"
THEME = ROOT / "boot/grub/boppos"
FIXTURE = ROOT / "tests/fixtures/fedora-grub.cfg"


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    loader = importlib.machinery.SourceFileLoader("bopp_grub_theme", str(SCRIPT))
    spec = importlib.util.spec_from_loader("bopp_grub_theme", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def boot(tmp_path):
    grub = tmp_path / "grub2"
    (grub / "fonts").mkdir(parents=True)
    (grub / "fonts/unicode.pf2").write_bytes(b"font")
    shutil.copy(FIXTURE, grub / "grub.cfg")
    return tmp_path


def test_install_copies_the_theme_and_switches_it_on(tool, boot):
    tool.install(boot, THEME, dry_run=False, say=lambda *_: None)
    theme = boot / "grub2/themes/boppos"
    assert {p.name for p in theme.iterdir()} == {"theme.txt", "mark.png", "lockup.png"}
    cfg = (boot / "grub2/user.cfg").read_text()
    assert cfg.count(tool.BEGIN.split(" (")[0]) == 1 and cfg.strip().endswith(tool.END)
    assert "set theme=${prefix}/themes/boppos/theme.txt" in cfg and "terminal_output gfxterm" in cfg
    assert "load_video" in cfg  # this grub.cfg defines it, so the block uses it
    assert "if loadfont unicode" in cfg and cfg.index("loadfont unicode") < cfg.index("terminal_output gfxterm")


def test_install_is_idempotent_and_leaves_no_temporary_files(tool, boot):
    tool.install(boot, THEME, False, say=lambda *_: None)
    before = (boot / "grub2/user.cfg").read_text()
    assert tool.install(boot, THEME, False, say=lambda *_: None) is False  # nothing to do the second time
    assert (boot / "grub2/user.cfg").read_text() == before
    leftovers = [p.name for p in (boot / "grub2").rglob("*") if p.name.startswith(".")]
    assert leftovers == [] and not list((boot / "grub2").glob("user.cfg.bopp-backup-*"))


def test_existing_user_cfg_is_kept_and_backed_up_once(tool, boot):
    user = boot / "grub2/user.cfg"
    user.write_text("GRUB2_PASSWORD=grub.pbkdf2.sha512.10000.ABC\nset foo=bar\n")
    tool.install(boot, THEME, False, say=lambda *_: None)
    text = user.read_text()
    assert text.startswith("GRUB2_PASSWORD=grub.pbkdf2.sha512.10000.ABC\nset foo=bar\n")  # untouched, still first
    backups = list((boot / "grub2").glob("user.cfg.bopp-backup-*"))
    assert len(backups) == 1 and backups[0].read_text().startswith("GRUB2_PASSWORD=")
    tool.install(boot, THEME, False, say=lambda *_: None)
    assert len(list((boot / "grub2").glob("user.cfg.bopp-backup-*"))) == 1  # not again


def test_a_changed_theme_is_replaced_and_the_block_is_rewritten_not_duplicated(tool, boot, tmp_path):
    tool.install(boot, THEME, False, say=lambda *_: None)
    newer = tmp_path / "newer"
    shutil.copytree(THEME, newer)
    (newer / "theme.txt").write_text((THEME / "theme.txt").read_text() + "\n# v2\n")
    assert tool.install(boot, newer, False, say=lambda *_: None) is True
    assert "# v2" in (boot / "grub2/themes/boppos/theme.txt").read_text()
    assert (boot / "grub2/user.cfg").read_text().count(tool.BEGIN.split(" (")[0]) == 1


def test_remove_restores_the_plain_menu_and_keeps_other_settings(tool, boot):
    user = boot / "grub2/user.cfg"
    user.write_text("GRUB2_PASSWORD=x\n")
    tool.install(boot, THEME, False, say=lambda *_: None)
    tool.remove(boot, False, say=lambda *_: None)
    assert user.read_text() == "GRUB2_PASSWORD=x\n" and not (boot / "grub2/themes/boppos").exists()


def test_remove_deletes_a_user_cfg_that_only_held_our_block(tool, boot):
    tool.install(boot, THEME, False, say=lambda *_: None)
    tool.remove(boot, False, say=lambda *_: None)
    assert not (boot / "grub2/user.cfg").exists() and not (boot / "grub2/themes").exists()
    assert tool.remove(boot, False, say=lambda *_: None) is False  # and removing twice is harmless
    (boot / "grub2/themes/other").mkdir(parents=True)  # another theme in there must survive
    tool.install(boot, THEME, False, say=lambda *_: None)
    tool.remove(boot, False, say=lambda *_: None)
    assert (boot / "grub2/themes/other").is_dir()


def test_dry_run_writes_nothing(tool, boot):
    lines = []
    assert tool.install(boot, THEME, True, say=lines.append) is True
    assert not (boot / "grub2/themes").exists() and not (boot / "grub2/user.cfg").exists()
    assert any("dry run" in x for x in lines)
    tool.install(boot, THEME, False, say=lambda *_: None)
    snapshot = sorted(str(p.relative_to(boot)) for p in boot.rglob("*"))
    tool.remove(boot, True, say=lambda *_: None)
    assert sorted(str(p.relative_to(boot)) for p in boot.rglob("*")) == snapshot


def test_a_layout_that_does_not_source_user_cfg_is_refused_without_writing(tool, boot):
    (boot / "grub2/grub.cfg").write_text("set timeout=3\nmenuentry 'x' { true }\n")
    with pytest.raises(tool.ThemeError, match="user.cfg"):
        tool.install(boot, THEME, False, say=lambda *_: None)
    assert not (boot / "grub2/themes").exists() and not (boot / "grub2/user.cfg").exists()


def test_an_arch_style_boot_directory_is_recognised_but_refused_with_advice(tool, tmp_path):
    (tmp_path / "grub").mkdir()
    (tmp_path / "grub/grub.cfg").write_text("set timeout=5\n")
    with pytest.raises(tool.ThemeError, match="GRUB_THEME"):
        tool.install(tmp_path, THEME, False, say=lambda *_: None)


def test_missing_grub_cfg_and_a_broken_theme_source_stop_cleanly(tool, tmp_path, boot):
    with pytest.raises(tool.ThemeError, match="no grub.cfg"):
        tool.install(tmp_path / "nowhere", THEME, False, say=lambda *_: None)
    broken = tmp_path / "broken"
    shutil.copytree(THEME, broken)
    (broken / "mark.png").unlink()
    with pytest.raises(tool.ThemeError, match="mark.png"):
        tool.install(boot, broken, False, say=lambda *_: None)
    assert not (boot / "grub2/user.cfg").exists()


def test_without_a_load_video_function_the_block_loads_video_modules_itself(tool, boot):
    cfg = (boot / "grub2/grub.cfg").read_text().replace("function load_video", "function other_thing")
    (boot / "grub2/grub.cfg").write_text(cfg)
    tool.install(boot, THEME, False, say=lambda *_: None)
    text = (boot / "grub2/user.cfg").read_text()
    assert "insmod all_video" in text and "\n  load_video\n" not in text


def test_a_missing_font_only_warns_because_the_block_guards_itself(tool, boot):
    (boot / "grub2/fonts/unicode.pf2").unlink()
    notes = []
    tool.install(boot, THEME, False, say=notes.append)
    assert any("unicode.pf2" in n for n in notes) and "if loadfont unicode" in (boot / "grub2/user.cfg").read_text()


def test_status_reports_each_stage(tool, boot):
    st = tool.status(boot, THEME)
    assert st["user_cfg_sourced"] and not st["theme_installed"] and not st["switched_on"] and st["font_available"]
    tool.install(boot, THEME, False, say=lambda *_: None)
    st = tool.status(boot, THEME)
    assert st["theme_installed"] and st["theme_up_to_date"] and st["switched_on"]


def test_the_cli_prints_errors_and_returns_nonzero(tool, tmp_path, capsys):
    assert tool.main(["install", "--boot", str(tmp_path), "--source", str(THEME)]) == 1
    assert "no grub.cfg" in capsys.readouterr().err
    assert tool.main(["status", "--boot", str(tmp_path)]) == 1
