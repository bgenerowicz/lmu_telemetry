"""Comparing car setups, setting by setting.

Setups come from :func:`lmu_core.carmodel.setup_values`: each setting the car
has, by its game key, as the game shows it. Car-wide settings are ``VM_*``
(``VM_REAR_WING``); per-wheel ones are ``WM_<name>-W_<corner>``
(``WM_CAMBER-W_FL``). Rows here name them in plain words, grouped by area, and
join the four wheels into one row (or front and rear) where they're the same.
"""

from __future__ import annotations

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
    # One per setup compared; None where that lap has no setup or no such setting.
    values: tuple[str | None, ...]

    @property
    def differs(self) -> bool:
        return len({v for v in self.values if v is not None}) > 1


def compare(setups: list[dict[str, str] | None]) -> list[SetupRow]:
    """The settings of several setups side by side, grouped by area. Settings
    none of them has or can change (N/A, fixed) are left out."""
    keys = list(dict.fromkeys(k for s in setups if s for k in s if not k.startswith(_SKIP)))
    car_wide = [k for k in keys if not _is_wheel(k)]
    wheels = list(dict.fromkeys(_wheel_name(k) for k in keys if _is_wheel(k)))
    rows = []
    for key in car_wide:
        rows.append(SetupRow(_group(key), _name(key), _values(setups, key)))
    for name in wheels:
        corner = {c: _values(setups, f"WM_{name}-W_{c}") for c in _CORNERS}
        if corner["FL"] == corner["FR"] and corner["RL"] == corner["RR"]:
            if corner["FL"] == corner["RL"]:
                parts = [("", corner["FL"])]
            else:
                parts = [(" front", corner["FL"]), (" rear", corner["RL"])]
        else:
            parts = [(f" {words}", corner[c]) for c, words in _CORNERS.items()]
        rows += [SetupRow(_group(name), _name(name) + suffix, values) for suffix, values in parts]
    rows = [r for r in rows if any(v not in _UNSET for v in r.values if v is not None)]
    order = [g for g, _ in _GROUPS] + ["Other"]
    return sorted(rows, key=lambda r: order.index(r.group))


def _values(setups: list[dict[str, str] | None], key: str) -> tuple[str | None, ...]:
    return tuple(s.get(key) if s else None for s in setups)


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
