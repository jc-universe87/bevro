"""Automations: Bevro owns when work happens.

    Agents do work. Bevro decides when.

A provider that can answer "check the competitor landscape" can be asked the
same thing every Monday without being changed in any way: the recurrence,
the clock, the condition and the history all belong to Bevro. Nothing is ever
built into a provider, generated or connected.
"""

from app.automations.conditions import ConditionSpec, evaluate
from app.automations.schedule import ScheduleSpec, next_occurrence, parse_schedule

__all__ = ["ConditionSpec", "ScheduleSpec", "evaluate", "next_occurrence", "parse_schedule"]
