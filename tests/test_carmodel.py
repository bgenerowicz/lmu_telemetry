"""Car models from a recording's entry name and setup."""

import json
from pathlib import Path

import duckdb
import pytest

from lmu_core.carmodel import car_model, fingerprint

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
