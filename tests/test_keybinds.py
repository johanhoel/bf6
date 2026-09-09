"""Tests for reading Battlefield 6's real key-binding format.

See keybinds.py's module docstring and data/keybind_concepts.json's `note`
for how the DIK_* keyboard decode was confirmed (cross-referenced against a
live BF6 install, not borrowed from another Battlefield title - BF4's own
documented scheme contradicts BF6's).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bf6tuner import keybinds  # noqa: E402

SAMPLE = """
GstKeyBinding.infantry.ConceptMapSize.0.axis 0
GstKeyBinding.infantry.ConceptMapSize.0.button 50
GstKeyBinding.infantry.ConceptMapSize.0.negate 0
GstKeyBinding.infantry.ConceptMapSize.0.type 2
GstKeyBinding.infantry.ConceptScoreboard.0.axis 0
GstKeyBinding.infantry.ConceptScoreboard.0.button 255
GstKeyBinding.infantry.ConceptScoreboard.0.negate 0
GstKeyBinding.infantry.ConceptScoreboard.0.type 2
GstKeyBinding.infantry.ConceptCyclePrimaryWeapon.0.axis 56
GstKeyBinding.infantry.ConceptCyclePrimaryWeapon.0.button 8
GstKeyBinding.infantry.ConceptCyclePrimaryWeapon.0.negate 0
GstKeyBinding.infantry.ConceptCyclePrimaryWeapon.0.type 0
GstKeyBinding.gunner.ConceptSomeGunnerThing.0.axis 0
GstKeyBinding.gunner.ConceptSomeGunnerThing.0.button 16
GstKeyBinding.gunner.ConceptSomeGunnerThing.0.negate 0
GstKeyBinding.gunner.ConceptSomeGunnerThing.0.type 2
GstAudio.Volume 0.420000
"""


def test_parse_keybindings_groups_by_category_and_concept():
    parsed = keybinds.parse_keybindings(SAMPLE)
    assert ("infantry", "ConceptMapSize") in parsed
    assert ("gunner", "ConceptSomeGunnerThing") in parsed
    assert parsed[("infantry", "ConceptMapSize")][0] == {
        "axis": 0, "button": 50, "negate": 0, "type": 2,
    }


def test_parse_keybindings_ignores_non_keybinding_lines():
    parsed = keybinds.parse_keybindings(SAMPLE)
    assert all(not concept.startswith("Volume") for _, concept in parsed)


def test_read_keybindings_decodes_confirmed_keyboard_codes():
    bindings = {b.concept: b for b in keybinds.read_keybindings(SAMPLE)}
    names = keybinds.dik_names()

    big_map = bindings["ConceptMapSize"]
    assert big_map.label == "Big Map"
    assert big_map.label_confirmed is True
    assert big_map.display_slots(names) == ["M"]


def test_read_keybindings_marks_unbound():
    bindings = {b.concept: b for b in keybinds.read_keybindings(SAMPLE)}
    names = keybinds.dik_names()
    assert bindings["ConceptScoreboard"].display_slots(names) == ["Unbound"]
    assert bindings["ConceptScoreboard"].slots[0].unbound is True


def test_read_keybindings_never_guesses_undecoded_device_types():
    """type 0/1 (mouse) is honestly not decoded - must say so, not invent
    a key name."""
    bindings = {b.concept: b for b in keybinds.read_keybindings(SAMPLE)}
    names = keybinds.dik_names()
    display = bindings["ConceptCyclePrimaryWeapon"].display_slots(names)[0]
    assert "not decoded" in display
    assert "axis=56" in display and "button=8" in display


def test_read_keybindings_includes_every_category_present():
    bindings = keybinds.read_keybindings(SAMPLE)
    categories = {b.category for b in bindings}
    assert categories == {"infantry", "gunner"}


def test_read_keybindings_falls_back_to_raw_concept_name_when_unlabelled():
    """An unlabelled real binding must still show up, not vanish."""
    bindings = {b.concept: b for b in keybinds.read_keybindings(SAMPLE)}
    gunner_thing = bindings["ConceptSomeGunnerThing"]
    assert gunner_thing.label == "ConceptSomeGunnerThing"
    assert gunner_thing.label_confirmed is False


def test_dik_names_table_matches_confirmed_cross_references():
    """The exact 6 bindings cross-referenced against a live BF6 install -
    see data/keybind_concepts.json's note. Regressing any of these means
    the table itself was edited without re-confirming."""
    names = keybinds.dik_names()
    assert names[50] == "M"       # Big Map
    assert names[21] == "Y"       # Quick Loadout Customization
    assert names[18] == "E"       # Interact/Quick Upgrade, Enter/Exit Vehicle
    assert names[15] == "Tab"     # Toggle Inventory / Scoreboard
    assert names[34] == "G"       # Throw Grenade
    assert names[59] == "F1"      # Vehicle Seat 1
    assert names[255] is None     # the "unbound" sentinel, never a real key
