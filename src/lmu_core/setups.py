"""Comparing car setups, setting by setting.

Setups come from :func:`lmu_core.carmodel.setup_values`: each setting the car
has, by its game key, with its step on the setting's scale and the text the
game shows. The text depends on the driver's units ("136 kPa", "19.7 psi"), so
on the same car model a setting differs only if its step does too. Car-wide settings are ``VM_*``
(``VM_REAR_WING``); per-wheel ones are ``WM_<name>-W_<corner>``
(``WM_CAMBER-W_FL``). Rows here name them in plain words, grouped by area, and
join the four wheels into one row (or front and rear) where they're the same.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Not part of how the car drives: pit stop plans, and the tank size (shown with
# a made-up lap count).
_SKIP = ("VM_NUM_PITSTOPS", "VM_PITSTOP_", "VM_FUEL_CAPACITY")
# Values that mean the car doesn't have the setting, or it can't be changed.
_UNSET = {"", "N/A", "Non-adjustable", "Fixed"}

# Names that read badly when spelled out from the key.
_NAMES = {
    "ANTILOCKBRAKESYSTEMMAP": "ABS map",
    "ANTILOCK_BRAKES": "ABS",
    "TRACTIONCONTROLMAP": "TC map",
    "TRACTIONCONTROLPOWERCUTMAP": "TC power cut",
    "TRACTIONCONTROLSLIPANGLEMAP": "TC slip angle",
    "TRACTION_CONTROL": "Traction control",
    "BRAKE_DUCTS": "Front brake ducts",
    "BRAKE_DUCTS_REAR": "Rear brake ducts",
    "FUEL_LEVEL": "Fuel",
    "GEAR_AUTODOWNSHIFT": "Auto downshift",
    "GEAR_AUTOUPSHIFT": "Auto upshift",
    "STEER_LOCK": "Steering lock",
    "OIL_RADIATOR": "Oil radiator",
    "WATER_RADIATOR": "Water radiator",
    "BRAKEDISC": "Brake disc thickness",
    "BRAKEPAD": "Brake pads",
    "COMPOUND": "Tyre compound",
    "PRESSURE": "Tyre pressure",
    "RIDEHEIGHT": "Ride height",
    "SRUBBER": "Spring rubber",
    "TENDERSPRINGTRAVEL": "Tender spring travel",
}
_WORDS = {
    "ANTISWAY": "anti-roll bar", "TOEIN": "toe", "TOEOFFSET": "toe offset", "3RD": "third spring",
    "TIRE": "tyre", "FASTBUMP": "fast bump", "FASTREBOUND": "fast rebound",
    "SLOWBUMP": "slow bump", "SLOWREBOUND": "slow rebound", "TENDERSPRING": "tender spring",
    "TENDERSPRINGTRAVEL": "tender spring travel", "BRAKEMAP": "brake map", "DIFF": "diff",
}
# The steering lock setting shows the wheels' angle in brackets: "524deg (18.5deg)",
# "536 (18) deg".
_WHEEL_LOCK = re.compile(r"\(\s*(\d+(?:\.\d+)?)")
_CORNERS = {"FL": "front left", "FR": "front right", "RL": "rear left", "RR": "rear right"}

# Areas, in the order shown; a setting goes in the first whose words it has.
_GROUPS = [
    ("Driver settings", ("ANTILOCK", "TRACTION", "BRAKE_BALANCE", "BRAKE_MIGRATION",
                         "BRAKE_PRESSURE", "ENGINE", "ELECTRIC", "REGEN", "VIRTUAL_ENERGY",
                         "FUEL", "STEER_LOCK", "REV_LIMITER")),
    ("Tyres", ("COMPOUND", "PRESSURE")),
    ("Aero and cooling", ("WING", "FENDER", "RADIATOR", "DUCTS")),
    ("Alignment", ("CAMBER", "TOE", "CASTER")),
    ("Suspension", ("SPRING", "ANTISWAY", "3RD", "RIDEHEIGHT", "PACKERS", "BUMP", "REBOUND",
                    "SRUBBER", "TRACK_BAR", "WEIGHT", "WHEEL_TRACK")),
    ("Brakes", ("BRAKEDISC", "BRAKEPAD")),
    ("Drivetrain", ("GEAR", "RATIO", "DIFF", "TORQUE_SPLIT")),
]


@dataclass(frozen=True)
class SetupRow:
    group: str
    name: str
    # As the game shows it, one per setup compared; None where that lap has no
    # setup or no such setting.
    values: tuple[str | None, ...]
    differs: bool


def compare(setups: list[dict[str, dict] | None], same_model: bool = True) -> list[SetupRow]:
    """The settings of several setups side by side, grouped by area. Settings
    none of them has or can change (N/A, fixed) are left out. Settings differ
    by text, and on the same car model by step as well (so not by units)."""
    keys = list(dict.fromkeys(k for s in setups if s for k in s if not k.startswith(_SKIP)))
    car_wide = [k for k in keys if not _is_wheel(k)]
    wheels = list(dict.fromkeys(_wheel_name(k) for k in keys if _is_wheel(k)))
    parts = [(_group(key), _name(key), _settings(setups, key)) for key in car_wide]
    for name in wheels:
        corner = {c: _settings(setups, f"WM_{name}-W_{c}") for c in _CORNERS}
        if corner["FL"] == corner["FR"] and corner["RL"] == corner["RR"]:
            if corner["FL"] == corner["RL"]:
                joined = [("", corner["FL"])]
            else:
                joined = [(" front", corner["FL"]), (" rear", corner["RL"])]
        else:
            joined = [(f" {words}", corner[c]) for c, words in _CORNERS.items()]
        parts += [(_group(name), _name(name) + suffix, each) for suffix, each in joined]
    rows = []
    for group, name, each in parts:
        texts = tuple(text for _, text in each)
        if not any(t not in _UNSET for t in texts if t is not None):
            continue
        present = [(step, text) for step, text in each if text is not None]
        differs = len({text for _, text in present}) > 1
        if same_model and all(step is not None for step, _ in present):
            # Other units show the same step differently; and a compound's step
            # can differ between sessions (which compounds are available) for
            # the same tyre. Only both differing is a different setting.
            differs = differs and len({step for step, _ in present}) > 1
        rows.append(SetupRow(group, name, texts, differs))
    order = [g for g, _ in _GROUPS] + ["Other"]
    return sorted(rows, key=lambda r: order.index(r.group))


def wheel_lock_deg(setup: dict | None) -> float | None:
    """The front wheels' angle at full steering lock (degrees), from the steering
    lock setting; None if the setup or the setting isn't there."""
    item = (setup or {}).get("VM_STEER_LOCK")
    text = item.get("text") if isinstance(item, dict) else item
    match = _WHEEL_LOCK.search(text or "")
    return float(match.group(1)) if match else None


def _settings(setups: list[dict | None], key: str) -> tuple[tuple[object, str | None], ...]:
    """Each setup's (step, text) for a setting; (None, None) where it's missing.
    A bare text (no step) is compared as text."""
    out = []
    for s in setups:
        item = s.get(key) if s else None
        if isinstance(item, dict):
            out.append((item.get("value"), item.get("text")))
        else:
            out.append((None, item))
    return tuple(out)


def _is_wheel(key: str) -> bool:
    return key.startswith("WM_") and "-W_" in key


def _wheel_name(key: str) -> str:
    return key.removeprefix("WM_").split("-W_")[0]


def _name(key: str) -> str:
    key = key.removeprefix("VM_")
    if key in _NAMES:
        return _NAMES[key]
    words = " ".join(_WORDS.get(w, w.lower()) for w in key.split("_"))
    return words[0].upper() + words[1:]


def _group(key: str) -> str:
    key = key.removeprefix("VM_")
    return next((g for g, words in _GROUPS if any(w in key for w in words)), "Other")
