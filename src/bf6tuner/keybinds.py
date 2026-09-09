"""Reads and decodes Battlefield 6's real key-binding storage format from
PROFSAVE_profile: ``GstKeyBinding.infantry.<Concept>.<slot>.<field>`` where
``<field>`` is one of ``axis``, ``button``, ``negate``, ``type``.

Read-only, on purpose - see the module docstring's last paragraph. Data
sourced from ``data/keybind_concepts.json``; see that file's own ``note``
for exactly what's confirmed and how (cross-referencing a live BF6 install's
real Edit Key Bindings menu against the raw file, then the public
DirectInput DIK_* scan code standard) versus what's honestly still an
inferred label. Frostbite's binding scheme is not stable across Battlefield
titles - Battlefield 4's own documented ``type`` meaning contradicts what
BF6 actually uses - so nothing here is borrowed from another game.

**Why read-only.** ``type`` 2 (keyboard) bindings are confidently decodable:
five independent bindings from a live profile matched the public DIK_*
table exactly. ``type`` 0/1 (mouse) and 3/4 (controller) are not decoded at
all - a real attempt to cross-reference mouse bindings hit a genuine data
gap (an unvisited keybind page in the in-game menu never gets serialized to
PROFSAVE_profile at all, so its concept doesn't even appear in the file to
check). A confirmed *read* for keyboard is not the same confidence bar as a
safe *write* for anything - getting a write wrong here means silently
breaking someone's real controls, which is a much worse failure than a
read-only display being incomplete. Promote this to read+write only after
that same bar is met for the write path specifically.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DATA_FILE = "keybind_concepts.json"

_LINE_RE = re.compile(
    r"^GstKeyBinding\.(?P<category>[^.]+)\.(?P<concept>[^.]+)\.(?P<slot>\d+)\.(?P<field>axis|button|negate|type)\s+(?P<value>\S+)\s*$"
)


def _resource_root() -> Path:
    """Mirrors database.py's frozen/source split - see that module."""
    import sys
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parent.parent.parent


def _load_concepts_data() -> dict[str, Any]:
    for candidate_dir in (_resource_root() / "data", Path(__file__).resolve().parents[2] / "data"):
        path = candidate_dir / _DATA_FILE
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Could not find {_DATA_FILE} under {_resource_root()} or its source checkout")


@dataclass
class BindingSlot:
    """One physical input bound to a concept - most concepts have exactly
    one slot (index 0); a few have two or more (primary + alternate)."""
    slot: int
    device_type: int  # raw 'type' field: 2=keyboard (decoded), else undecoded
    raw_button: int
    raw_axis: int
    negate: bool

    @property
    def unbound(self) -> bool:
        return self.raw_button == 255

    def display(self, dik_names: dict[int, str | None]) -> str:
        if self.unbound:
            return "Unbound"
        if self.device_type == 2:
            name = dik_names.get(self.raw_button)
            return name if name else f"Keyboard (code {self.raw_button})"
        if self.device_type in (0, 1):
            return f"Mouse (raw axis={self.raw_axis} button={self.raw_button}, type={self.device_type} - not decoded)"
        return f"Controller? (raw axis={self.raw_axis} button={self.raw_button}, type={self.device_type} - not decoded)"


@dataclass
class ConceptBinding:
    category: str
    concept: str
    label: str
    label_confirmed: bool
    slots: list[BindingSlot] = field(default_factory=list)

    def display_slots(self, dik_names: dict[int, str | None]) -> list[str]:
        return [slot.display(dik_names) for slot in self.slots]


def parse_keybindings(profsave_text: str) -> dict[tuple[str, str], dict[int, dict[str, Any]]]:
    """Pure parse: {(category, concept): {slot_index: {"axis":.., "button":.., "negate":.., "type":..}}}.

    Keeps every category found, not just ``infantry`` - a real BF6 profile
    was first seen with only ``infantry`` present, which looked at the time
    like the game's one unified scheme (no separate vehicle/heli/jet
    concepts like older titles), but a later screenshot showed a distinct
    "GUNNER KEYBINDS" page, proving there are more. The real explanation for
    why only ``infantry`` showed up at first: a category's bindings are
    only ever serialized to the file once its settings page has actually
    been visited in-game (confirmed independently - see the data file's
    note on "Gadget One/Two" never appearing despite being bound in the
    UI). So the honest fix is "don't filter categories at all," not
    "hardcode the two now known" - more will surface as more pages get
    visited, by this user or anyone else. Kept separate from the friendly-
    label lookup so this half is testable against a hand-built payload with
    zero dependency on the concept/label data file (which only has
    ``infantry`` labels so far).
    """
    result: dict[tuple[str, str], dict[int, dict[str, Any]]] = {}
    for line in profsave_text.splitlines():
        match = _LINE_RE.match(line.strip())
        if not match:
            continue
        key = (match.group("category"), match.group("concept"))
        slot_index = int(match.group("slot"))
        slot_data = result.setdefault(key, {}).setdefault(slot_index, {})
        raw_value = match.group("value")
        try:
            slot_data[match.group("field")] = int(float(raw_value))
        except ValueError:
            continue
    return result


def read_keybindings(profsave_text: str) -> list[ConceptBinding]:
    """Every real bound concept found in `profsave_text`, with a friendly
    label where one is known (see data/keybind_concepts.json). A concept
    with no entry in that file still shows up, labelled with its raw
    internal name - never silently dropped, since an unlabelled real
    binding is more useful than a labelled fake one.
    """
    concepts_data = _load_concepts_data()
    known = concepts_data.get("concepts", {})
    parsed = parse_keybindings(profsave_text)

    bindings: list[ConceptBinding] = []
    for (category, concept), slots_by_index in parsed.items():
        # Label lookup is by concept name alone (only "infantry" is labelled
        # so far - see data file note) - if some other category ever reuses
        # a concept name for something different, this would mislabel it;
        # not observed yet, and not worth the complexity until it is.
        info = known.get(concept, {})
        slots = []
        for slot_index in sorted(slots_by_index):
            raw = slots_by_index[slot_index]
            if not all(k in raw for k in ("axis", "button", "negate", "type")):
                continue  # incomplete slot (shouldn't happen, but don't guess)
            slots.append(BindingSlot(
                slot=slot_index, device_type=raw["type"], raw_button=raw["button"],
                raw_axis=raw["axis"], negate=bool(raw["negate"]),
            ))
        if not slots:
            continue
        bindings.append(ConceptBinding(
            category=category,
            concept=concept,
            label=info.get("label", concept),
            label_confirmed=bool(info.get("confirmed", False)),
            slots=slots,
        ))

    bindings.sort(key=lambda b: b.label)
    return bindings


def dik_names() -> dict[int, str | None]:
    """{scan code: friendly name} from the confirmed public DIK_* table -
    see data/keybind_concepts.json's note for the cross-reference."""
    raw = _load_concepts_data().get("dik_names", {})
    return {int(code): name for code, name in raw.items()}
