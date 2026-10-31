"""Core analysis shared by the app and the server.

Pipeline: read DuckDB recording -> split into laps -> resample by distance ->
segment into corners/straights -> compare against a reference -> advice.
"""

from lmu_core.lapfacts import LapFacts
from lmu_core.ruleset import CheckReport, Rule, RuleResult, Ruleset

__all__ = ["CheckReport", "LapFacts", "Rule", "RuleResult", "Ruleset"]
