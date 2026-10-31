"""Car models from a recording's entry name and setup."""

import json
from pathlib import Path

import duckdb
import pytest

from lmu_core.carmodel import car_model, fingerprint, setup_is_fixed, setup_values

ALGARVE = Path(__file__).parent.parent / "samples" / (
    "Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb"
)


@pytest.mark.skipif(not ALGARVE.exists(), reason="sample recording not present")
def test_model_from_setup_whatever_the_livery():
    meta = dict(duckdb.connect(str(ALGARVE), read_only=True)
                .execute("SELECT * FROM metadata").fetchall())
    assert car_model("Garage 59 2026 #10:LM", meta["CarSetup"]) == "McLaren 720S GT3"
    assert car_model("Anything at all", meta["CarSetup"]) == "McLaren 720S GT3"


def test_unknown_setups_fall_back_to_the_name():
    setup = json.dumps({"VM_FRONT_WING": {"minValue": 0, "maxValue": 99}})
    assert fingerprint(setup) is not None
    assert car_model("Ferrari Custom Team 2026 #12", setup) == "Ferrari"
    assert car_model("Mustang Custom Team 2026 #397", setup) == "Ford Mustang GT3"
    assert car_model("Some Team 2026 #7:LM", None) == "Some Team 2026 #7:LM"
    assert fingerprint("not json") is None


def test_fixed_setup_when_only_driver_settings_are_free():
    def setup(free_keys, total=10):
        return json.dumps({f"VM_{i}": {"available": True, "isFreeSetting": i < free_keys}
                           for i in range(total)})

    assert setup_is_fixed(setup(3)) is True
    assert setup_is_fixed(setup(9)) is False
    assert setup_is_fixed(None) is None


@pytest.mark.skipif(not ALGARVE.exists(), reason="sample recording not present")
def test_setup_values_as_the_game_shows_them():
    meta = dict(duckdb.connect(str(ALGARVE), read_only=True)
                .execute("SELECT * FROM metadata").fetchall())
    values = setup_values(meta["CarSetup"])
    assert values["VM_BRAKE_BALANCE"] == {"value": 18, "text": "52.5:47.5"}
    assert values["WM_CAMBER-W_FL"]["text"] == "-2.20 deg"
    assert setup_values(None) is None and setup_values("{}") is None
