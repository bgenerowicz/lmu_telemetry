"""Rulesets: the "standard conditions" a lap must meet to enter a leaderboard.

A ruleset is a YAML file (see ``rulesets/`` and ``docs/RULESETS.md``). Each rule
targets one field of :class:`~lmu_core.lapfacts.LapFacts` and constrains it with
``equals``, ``one_of`` and/or ``min``/``max``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lmu_core.lapfacts import LapFacts

LAP_FIELDS = frozenset(f.name for f in dataclasses.fields(LapFacts))

OnUnknown = Literal["reject", "warn"]
Scalar = str | float | bool


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equals: Scalar | None = None
    one_of: list[Scalar] | None = None
    min: float | None = None
    max: float | None = None
    # Overrides the ruleset-wide default when the lap doesn't provide this field.
    on_unknown: OnUnknown | None = None

    @model_validator(mode="after")
    def _has_constraint(self) -> Rule:
        if self.equals is None and self.one_of is None and self.min is None and self.max is None:
            raise ValueError("rule needs at least one of: equals, one_of, min, max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min ({self.min}) is greater than max ({self.max})")
        return self

    def describe(self) -> str:
        parts = []
        if self.equals is not None:
            parts.append(f"== {self.equals!r}")
        if self.one_of is not None:
            parts.append(f"in {self.one_of!r}")
        if self.min is not None:
            parts.append(f">= {self.min}")
        if self.max is not None:
            parts.append(f"<= {self.max}")
        return " and ".join(parts)

    def matches(self, value: Scalar) -> bool:
        if self.equals is not None and not _same(value, self.equals):
            return False
        if self.one_of is not None and not any(_same(value, v) for v in self.one_of):
            return False
        if self.min is not None and not (_is_number(value) and value >= self.min):
            return False
        if self.max is not None and not (_is_number(value) and value <= self.max):
            return False
        return True


def _is_number(value: Scalar) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _same(a: Scalar, b: Scalar) -> bool:
    # Game strings vary in case and padding ("Practice" vs "PRACTICE ").
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().casefold() == b.strip().casefold()
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


@dataclass(frozen=True)
class RuleResult:
    field: str
    status: Literal["pass", "fail", "unknown"]
    rejects: bool
    message: str


@dataclass(frozen=True)
class CheckReport:
    ruleset_id: str
    results: list[RuleResult]

    @property
    def accepted(self) -> bool:
        return not any(r.rejects for r in self.results)

    @property
    def problems(self) -> list[RuleResult]:
        return [r for r in self.results if r.status != "pass"]


class Ruleset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    top_n: int = Field(default=10, ge=1)
    leaderboard_key: list[str] = ["track", "layout", "car", "game_version"]
    # Keep top_n per value of these fields within a leaderboard (e.g. [car]: top N per
    # car on a class leaderboard) instead of top_n for the whole leaderboard.
    top_n_per: list[str] = []
    on_unknown: OnUnknown = "warn"
    rules: dict[str, Rule] = {}

    @field_validator("leaderboard_key")
    @classmethod
    def _key_fields_exist(cls, key: list[str]) -> list[str]:
        unknown = [k for k in key if k not in LAP_FIELDS]
        if unknown:
            raise ValueError(f"unknown leaderboard_key fields: {unknown}")
        if not key:
            raise ValueError("leaderboard_key must not be empty")
        return key

    @field_validator("top_n_per")
    @classmethod
    def _per_fields_exist(cls, per: list[str]) -> list[str]:
        unknown = [k for k in per if k not in LAP_FIELDS]
        if unknown:
            raise ValueError(f"unknown top_n_per fields: {unknown}")
        return per

    @field_validator("rules")
    @classmethod
    def _rule_fields_exist(cls, rules: dict[str, Rule]) -> dict[str, Rule]:
        unknown = [k for k in rules if k not in LAP_FIELDS]
        if unknown:
            raise ValueError(f"rules target unknown lap fields: {unknown}")
        return rules

    @classmethod
    def load(cls, path: str | Path) -> Ruleset:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(yaml.safe_load(f))

    def leaderboard_key_for(self, lap: LapFacts) -> tuple:
        return tuple(getattr(lap, k) for k in self.leaderboard_key)

    def check(self, lap: LapFacts) -> CheckReport:
        results = []
        for field, rule in self.rules.items():
            value = getattr(lap, field)
            expected = rule.describe()
            if value is None:
                rejects = (rule.on_unknown or self.on_unknown) == "reject"
                results.append(
                    RuleResult(field, "unknown", rejects, f"{field} unknown (needs {expected})")
                )
            elif rule.matches(value):
                results.append(RuleResult(field, "pass", False, f"{field}={value!r}"))
            else:
                results.append(
                    RuleResult(field, "fail", True, f"{field}={value!r}, needs {expected}")
                )
        return CheckReport(self.id, results)
