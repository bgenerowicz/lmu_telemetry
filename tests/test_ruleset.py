from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from lmu_core import LapFacts, Ruleset

RULESETS = Path(__file__).parent.parent / "rulesets"

GOOD_LAP = LapFacts(
    track="Sebring International Raceway",
    layout="12h",
    car="Oreca 07",
    game_version="1.2.0",
    session_type="Practice",
    weather="Clear",
    track_temp_c=28.0,
    setup_is_default=True,
    fuel_at_start_l=20.0,
    lap_time_s=118.4,
    is_valid=True,
    is_flying=True,
)


@pytest.fixture
def standard() -> Ruleset:
    return Ruleset.load(RULESETS / "standard-v1.yaml")


@pytest.mark.parametrize("path", sorted(RULESETS.glob("*.yaml")), ids=lambda p: p.name)
def test_shipped_rulesets_load(path):
    Ruleset.load(path)


def test_good_lap_accepted(standard):
    report = standard.check(GOOD_LAP)
    assert report.accepted
    assert report.problems == []


@pytest.mark.parametrize(
    "change",
    [
        {"session_type": "Race"},
        {"weather": "Rain"},
        {"track_temp_c": 40.0},
        {"setup_is_default": False},
        {"fuel_at_start_l": 80.0},
        {"is_valid": False},
        {"is_flying": False},
    ],
)
def test_breaking_a_rule_rejects(standard, change):
    report = standard.check(replace(GOOD_LAP, **change))
    assert not report.accepted
    [problem] = report.problems
    assert problem.field == next(iter(change))
    assert problem.status == "fail"


def test_unknown_warns_by_default(standard):
    report = standard.check(replace(GOOD_LAP, weather=None))
    assert report.accepted
    [problem] = report.problems
    assert problem.status == "unknown"
    assert not problem.rejects


def test_unknown_rejects_when_rule_says_so(standard):
    report = standard.check(replace(GOOD_LAP, is_valid=None))
    assert not report.accepted


def test_string_matching_ignores_case_and_padding(standard):
    assert standard.check(replace(GOOD_LAP, session_type=" PRACTICE ")).accepted


def test_bool_rule_does_not_match_number():
    ruleset = Ruleset(id="t", name="t", rules={"is_valid": {"equals": True}})
    assert not ruleset.rules["is_valid"].matches(1)


def test_leaderboard_key(standard):
    assert standard.leaderboard_key_for(GOOD_LAP) == (
        "Sebring International Raceway",
        "12h",
        "Oreca 07",
        "1.2.0",
    )


@pytest.mark.parametrize(
    "data",
    [
        {"rules": {"not_a_field": {"equals": 1}}},
        {"rules": {"weather": {}}},
        {"rules": {"track_temp_c": {"min": 30, "max": 20}}},
        {"rules": {"weather": {"one_of": ["Clear"], "typo": 1}}},
        {"leaderboard_key": ["track", "nope"]},
        {"leaderboard_key": []},
        {"top_n": 0},
    ],
)
def test_invalid_rulesets_rejected(data):
    with pytest.raises(ValidationError):
        Ruleset.model_validate({"id": "t", "name": "t", **data})
