import pytest
from boppos import system


def test_all_or_empty_means_the_default_set():
    assert system.parse_targets("all") is None
    assert system.parse_targets("") is None


def test_a_list_keeps_the_canonical_order_and_drops_duplicates():
    assert system.parse_targets("flatpak,os") == ["os", "flatpak"]
    assert system.parse_targets(" os , fw ,os") == ["os", "fw"]


def test_unknown_targets_are_refused():
    with pytest.raises(ValueError, match="unknown update target"):
        system.parse_targets("os,bios")
