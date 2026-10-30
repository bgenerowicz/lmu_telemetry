"""Which car model a recording is in, and whether its setup was fixed.

A recording's ``CarName`` is the entry picked in the game: a team and livery
("Garage 59 2026 #10:LM"), not the model. The setup (``CarSetup``) gives the
model away: each model has its own adjustment ranges, the same for every
livery of it. So the model is looked up by a fingerprint of those ranges;
failing that, custom liveries are named after their make ("McLaren Custom
Team 2025 #397"); failing that, the entry name stands in for the model.

The setup also says which settings could be changed (``isFreeSetting``). In a
fixed-setup session only the driver's settings can (aids, brake bias, fuel,
tyres, pit stops: ~30 of ~170); with an open setup nearly all of them. It
doesn't say whether an open setup was the game's default.
"""

from __future__ import annotations

import hashlib
import json
import re

# Fingerprints (see ``fingerprint``) of known models, from recordings of them.
KNOWN_MODELS = {
    "01f59341186d": "McLaren 720S GT3",  # Garage 59 #10, McLaren Custom Team
    "2f28f73a2058": "Mercedes-AMG GT3",  # Iron Lynx #61, Mercedes AMG Custom Team
    "855f7db31b7e": "Ford Mustang GT3",  # Proton #88, Mustang Custom Team
    "924e3d66b470": "Porsche 911 GT3 R",  # Manthey DK Engineering #91
}
# Makes in custom livery names, for models not fingerprinted yet.
_CUSTOM_TEAM = re.compile(r"^(.+?) Custom Team\b")
_MAKES = {
    "McLaren": "McLaren 720S GT3",
    "Mercedes AMG": "Mercedes-AMG GT3",
    "Mustang": "Ford Mustang GT3",
}


def car_model(car_name: str, car_setup: str | None) -> str:
    """The model of the car in a recording, from its ``CarName`` and ``CarSetup``."""
    model = KNOWN_MODELS.get(fingerprint(car_setup) or "")
    if model:
        return model
    match = _CUSTOM_TEAM.match(car_name or "")
    if match:
        return _MAKES.get(match.group(1), match.group(1))
    return car_name


def setup_is_fixed(car_setup: str | None) -> bool | None:
    """Whether the session fixed the setup (None if the setup isn't there)."""
    items = _setup_items(car_setup)
    usable = [item for item in items.values() if item.get("available")]
    if not usable:
        return None
    free = sum(1 for item in usable if item.get("isFreeSetting"))
    return free < len(usable) / 2


def _setup_items(car_setup: str | None) -> dict[str, dict]:
    try:
        setup = json.loads(car_setup or "")
    except ValueError:
        return {}
    if not isinstance(setup, dict):
        return {}
    return {k: v for k, v in setup.items() if k.startswith(("VM_", "WM_")) and isinstance(v, dict)}


def fingerprint(car_setup: str | None) -> str | None:
    """A hash of the setup's adjustment ranges, which only depend on the model."""
    ranges = sorted(
        (key, item.get("minValue"), item.get("maxValue"))
        for key, item in _setup_items(car_setup).items()
        if key.startswith("VM_")
    )
    if not ranges:
        return None
    return hashlib.sha1(json.dumps(ranges).encode()).hexdigest()[:12]
