"""The single entry point and what starts it. The GTK app is not imported: its option logic lives in plain functions
that this reads from the source, and the model pieces are tested in test_firstrun.py and test_welcome.py."""

import ast
from pathlib import Path

from boppos import firstrun, welcome

REPO = Path(__file__).parents[1]
APP = REPO / "lib/boppos/py/boppos/ui/app.py"


def first_run_flow():
    """first_run_flow() from ui/app.py, compiled on its own (importing the module needs GTK)."""
    tree = ast.parse(APP.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "first_run_flow")
    ns = {"firstrun": firstrun}
    exec(compile(ast.Module([node], []), str(APP), "exec"), ns)  # noqa: S102
    return ns["first_run_flow"]


def test_the_flags_map_to_flows(tmp_path, monkeypatch):
    flow = first_run_flow()
    monkeypatch.setattr(firstrun, "decide", lambda root="/": firstrun.Decision("welcome", "oobe"))
    assert flow({"first-run": True}) == "welcome"
    assert flow({"first-run": True, "oobe": True}) == "welcome"  # --first-run decides, forcing flags do not override
    assert flow({"oobe": True}) == "setup" and flow({"welcome": True}) == "welcome"
    assert flow({}, preview=True) == "welcome" and flow({}) == "none"
    monkeypatch.setattr(firstrun, "decide", lambda root="/": firstrun.Decision("none", None))
    assert flow({"first-run": True}) == "none"


def test_the_options_exist_and_the_old_ones_stay():
    src = APP.read_text()
    for opt in ("first-run", "welcome", "oobe", "hardware", "if-needed"):
        assert f'add_main_option("{opt}"' in src


def test_the_tour_asks_where_to_start_only_when_it_was_not_chosen():
    assert [s.key for s in welcome.steps_for(False)].count("session") == 0
    steps = [s.key for s in welcome.steps_for(False, ask_start_in=True)]
    assert steps.index("hardware") < steps.index("session") < steps.index("review")


def test_start_in_is_planned_and_applied_through_the_helper_before_the_apps():
    c = welcome.Choices(start_in="gaming", decky=True)
    assert welcome.build_plan(c)[0].text == "Sign in automatically to Gaming Mode"
    helper = lambda a: ["pkexec", "oobe-helper", *a]  # noqa: E731
    assert welcome.first_run_commands(c, "ripps", helper) == [
        ["pkexec", "oobe-helper", "set-start-in", "ripps", "gaming"]]
    assert welcome.first_run_commands(welcome.Choices(), "ripps", helper) == []
    assert welcome.finish_command(helper) == ["pkexec", "oobe-helper", "finish-first-run"]
    assert not welcome.Choices(start_in="desktop").is_empty()


def test_everything_that_starts_the_flow_uses_the_one_entry_point():
    desktop = (REPO / "autostart/bopp-welcome.desktop").read_text()
    assert "Exec=/usr/bin/boppos-deck --first-run\n" in desktop and "--if-needed" not in desktop
    unit = (REPO / "systemd/system/boppos-oobe.service").read_text()
    assert "ConditionPathExists" not in unit.replace("ConditionPathExists=/usr/bin/boppos-deck", "")  # no marker needed
    assert "oobe-done" not in unit
    assert "ExecCondition=/usr/libexec/boppos/oobe-needed" in unit and "boppos-deck --first-run" in unit
    assert "User=boppos-oobe" in unit
