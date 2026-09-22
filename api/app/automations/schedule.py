"""When work should happen, in the person's own words and in a shape Bevro can count on.

Nobody types cron. "Every Monday morning" becomes a ScheduleSpec, which knows
how to say when the next run is due — in a named timezone, so the hour a
person meant is the hour they get, through daylight saving and restarts alike.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

DEFAULT_TIME = time(9, 0)
DEFAULT_TIMEZONE = "UTC"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
# Words people use for a time of day, and what Bevro takes them to mean.
TIME_WORDS = {
    "morning": time(9, 0),
    "midday": time(12, 0),
    "noon": time(12, 0),
    "lunchtime": time(12, 30),
    "afternoon": time(14, 0),
    "evening": time(18, 0),
    "night": time(21, 0),
    "overnight": time(3, 0),
}


class Recurrence(StrEnum):
    MINUTES = "minutes"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ONCE = "once"


class ScheduleSpec(BaseModel):
    """A recurrence a person would recognise, and the clock it follows."""

    recurrence: Recurrence = Recurrence.DAILY
    # Every N days/weeks/months/hours; "every other Monday" is 2.
    every: int = Field(default=1, ge=1, le=52)
    at: time = DEFAULT_TIME
    # Weekly: which days. Monday is 0.
    weekdays: list[int] = Field(default_factory=list)
    # Monthly: which day of the month; 1 means the first. 31 means the last day.
    day_of_month: int = Field(default=1, ge=1, le=31)
    # For "every 15 minutes".
    minutes: int = Field(default=60, ge=5, le=1440)
    timezone: str = DEFAULT_TIMEZONE
    start_at: datetime | None = None
    end_at: datetime | None = None

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            return DEFAULT_TIMEZONE
        return value

    @property
    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(DEFAULT_TIMEZONE)

    def describe(self) -> str:
        """How the person sees it: "Every Monday · 09:00"."""
        clock = self.at.strftime("%H:%M")
        if self.recurrence == Recurrence.MINUTES:
            return f"Every {self.minutes} minutes" if self.minutes != 60 else "Every hour"
        if self.recurrence == Recurrence.HOURLY:
            return "Every hour" if self.every == 1 else f"Every {self.every} hours"
        if self.recurrence == Recurrence.DAILY:
            return f"Every day · {clock}" if self.every == 1 else f"Every {self.every} days · {clock}"
        if self.recurrence == Recurrence.WEEKLY:
            chosen = sorted(self.weekdays)
            prefix = "Every" if self.every == 1 else f"Every {self.every} weeks on"
            if self.every == 1 and chosen in ([0, 1, 2, 3, 4], [5, 6]):
                return f"Every {'weekday' if chosen[0] == 0 else 'weekend day'} · {clock}"
            days = ", ".join(WEEKDAYS[d].capitalize() for d in chosen) or "Monday"
            return f"{prefix} {days} · {clock}"
        if self.recurrence == Recurrence.MONTHLY:
            day = "last day" if self.day_of_month == 31 else _ordinal(self.day_of_month)
            return f"The {day} of every month · {clock}"
        return f"Once · {clock}"

    def canonical(self) -> str:
        """The recurrence in one line, for Advanced details only."""
        if self.recurrence == Recurrence.MINUTES:
            return f"FREQ=MINUTELY;INTERVAL={self.minutes}"
        if self.recurrence == Recurrence.HOURLY:
            return f"FREQ=HOURLY;INTERVAL={self.every}"
        if self.recurrence == Recurrence.DAILY:
            return f"FREQ=DAILY;INTERVAL={self.every};BYHOUR={self.at.hour};BYMINUTE={self.at.minute}"
        if self.recurrence == Recurrence.WEEKLY:
            days = ",".join(WEEKDAYS[d][:2].upper() for d in sorted(self.weekdays) or [0])
            return f"FREQ=WEEKLY;INTERVAL={self.every};BYDAY={days};BYHOUR={self.at.hour};BYMINUTE={self.at.minute}"
        if self.recurrence == Recurrence.MONTHLY:
            day = -1 if self.day_of_month == 31 else self.day_of_month
            return f"FREQ=MONTHLY;INTERVAL={self.every};BYMONTHDAY={day};BYHOUR={self.at.hour};BYMINUTE={self.at.minute}"
        return "FREQ=ONCE"


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


# --------------------------------------------------------------------------- when next

def _at_local(spec: ScheduleSpec, day: datetime) -> datetime:
    """The spec's time of day on that local date, as an instant.

    A wall-clock time that does not exist (the spring-forward hour) or happens
    twice (the autumn one) is resolved the way a person expects: the first
    moment at or after the time they asked for.
    """
    naive = datetime.combine(day.date(), spec.at)
    local = naive.replace(tzinfo=spec.zone)
    # If the zone skipped this hour, the conversion lands before the wall time
    # the person asked for; step forward until it does not.
    for _ in range(4):
        if local.astimezone(spec.zone).hour >= spec.at.hour or local.hour != spec.at.hour:
            break
        local += timedelta(hours=1)
    return local.astimezone(timezone.utc)


def next_occurrence(spec: ScheduleSpec, after: datetime, *, inclusive: bool = False) -> datetime | None:
    """The next moment this schedule is due, in UTC. None when it has ended."""
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    moment = after.astimezone(timezone.utc)
    if spec.start_at is not None and moment < spec.start_at:
        moment = spec.start_at
        inclusive = True

    if spec.recurrence == Recurrence.MINUTES:
        candidate = moment if inclusive else moment + timedelta(minutes=spec.minutes)
        return _within(spec, candidate)
    if spec.recurrence == Recurrence.HOURLY:
        candidate = moment if inclusive else moment + timedelta(hours=spec.every)
        return _within(spec, candidate)

    local = moment.astimezone(spec.zone)
    if spec.recurrence == Recurrence.DAILY:
        for offset in range(0, 366):
            candidate = _at_local(spec, local + timedelta(days=offset))
            if candidate > moment or (inclusive and candidate >= moment):
                if spec.every > 1 and spec.start_at is not None:
                    days = (candidate.astimezone(spec.zone).date() - spec.start_at.astimezone(spec.zone).date()).days
                    if days % spec.every != 0:
                        continue
                return _within(spec, candidate)
        return None
    if spec.recurrence == Recurrence.WEEKLY:
        wanted = sorted(spec.weekdays) or [0]
        for offset in range(0, 8 * max(spec.every, 1) + 7):
            day = local + timedelta(days=offset)
            if day.weekday() not in wanted:
                continue
            candidate = _at_local(spec, day)
            if candidate > moment or (inclusive and candidate >= moment):
                if spec.every > 1 and spec.start_at is not None:
                    weeks = ((candidate.astimezone(spec.zone).date() - spec.start_at.astimezone(spec.zone).date()).days) // 7
                    if weeks % spec.every != 0:
                        continue
                return _within(spec, candidate)
        return None
    if spec.recurrence == Recurrence.MONTHLY:
        year, month = local.year, local.month
        for _ in range(0, 26):
            last = monthrange(year, month)[1]
            day = last if spec.day_of_month >= 31 else min(spec.day_of_month, last)
            candidate = _at_local(spec, local.replace(year=year, month=month, day=day))
            if candidate > moment or (inclusive and candidate >= moment):
                return _within(spec, candidate)
            month += spec.every
            while month > 12:
                month -= 12
                year += 1
        return None
    # Once: only if it has not already happened.
    candidate = spec.start_at or _at_local(spec, local)
    return candidate if candidate > moment else None


def _within(spec: ScheduleSpec, candidate: datetime) -> datetime | None:
    if spec.end_at is not None and candidate > spec.end_at:
        return None
    return candidate


def catch_up(spec: ScheduleSpec, due: datetime, now: datetime) -> datetime | None:
    """The next due moment after a gap, skipping everything that was missed.

    Bevro runs the work once when it comes back, not once for every interval
    it was away.
    """
    return next_occurrence(spec, now)


# --------------------------------------------------------------------------- reading the person's words

_EVERY_N = re.compile(r"\bevery\s+(\d+)\s*(minute|min|hour|day|week|month)s?\b", re.I)
_EVERY_OTHER = re.compile(r"\bevery\s+other\s+(day|week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
_CLOCK = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b(?!\s*(?:minutes?|hours?|days?|weeks?|months?))", re.I)
_WEEKDAY = re.compile(r"\b(mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?)\b", re.I)
# "every weekday", "Mon-Fri", "each working day": Monday to Friday, said in several ways.
_WORKDAYS = re.compile(
    r"\b(?:every |each |on |at )?(week ?days?|working days?|business days?|work days?|"
    r"mon(?:day)?s?\s*(?:-|–|to|through|thru)\s*fri(?:day)?s?)\b", re.I)
_WEEKEND = re.compile(r"\b(?:every |each |on |at )?(week ?ends?)\b", re.I)
_MONTHLY = re.compile(r"\b(first|last|1st|(\d{1,2})(?:st|nd|rd|th))\s+(?:day\s+)?of\s+(?:every|each|the)\s+month\b|\bmonthly\b|\bevery month\b", re.I)
_DAILY = re.compile(r"\b(every ?day|each ?day|daily|every morning|each morning|every evening|each evening|every night)\b", re.I)
_WEEKLY = re.compile(r"\b(weekly|every week|each week)\b", re.I)
_HOURLY = re.compile(r"\b(hourly|every hour|each hour)\b", re.I)
_RECURRING_HINT = re.compile(r"\b(every|each|daily|weekly|monthly|hourly|week ?days?|working days?|week ?ends?|recurring|regularly|from now on|keep (?:an eye|checking|watching)|watch for|monitor)\b", re.I)
# Words that make it monitoring rather than a plain repeat.
_MONITOR = re.compile(r"\b(only (?:tell|let|notify|alert)|tell me (?:only )?(?:when|if)|let me know (?:when|if)|notify me (?:when|if)|alert me (?:when|if)|watch for|keep an eye|monitor)\b", re.I)


def has_recurring_intent(text: str) -> bool:
    """Did the person ask for this to happen again, not just now?"""
    return bool(_RECURRING_HINT.search(text or ""))


def _time_of_day(text: str) -> time | None:
    for word, value in TIME_WORDS.items():
        if re.search(rf"\b{word}\b", text, re.I):
            return value
    for match in _CLOCK.finditer(text):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if match.group(2) is None and not meridiem and not re.search(r"\bat\s+" + re.escape(match.group(1)), text, re.I):
            continue  # a bare number that is not a time ("3 competitors")
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
    return None


def parse_schedule(text: str, *, tz: str = DEFAULT_TIMEZONE, now: datetime | None = None) -> ScheduleSpec | None:
    """The recurrence a sentence asks for, or None if it asks for none."""
    text = " ".join((text or "").split())
    if not text:
        return None
    at = _time_of_day(text) or DEFAULT_TIME
    spec = ScheduleSpec(at=at, timezone=tz, start_at=(now or datetime.now(timezone.utc)))

    other = _EVERY_OTHER.search(text)
    if other:
        unit = other.group(1).lower()
        spec.every = 2
        if unit in WEEKDAYS:
            spec.recurrence = Recurrence.WEEKLY
            spec.weekdays = [WEEKDAYS.index(unit)]
        else:
            spec.recurrence = {"day": Recurrence.DAILY, "week": Recurrence.WEEKLY, "month": Recurrence.MONTHLY}[unit]
            if spec.recurrence == Recurrence.WEEKLY:
                spec.weekdays = [(now or datetime.now(timezone.utc)).astimezone(spec.zone).weekday()]
        return spec

    every_n = _EVERY_N.search(text)
    if every_n:
        count, unit = int(every_n.group(1)), every_n.group(2).lower()
        if unit.startswith("min"):
            spec.recurrence = Recurrence.MINUTES
            spec.minutes = max(5, min(count, 1440))
        elif unit == "hour":
            spec.recurrence = Recurrence.HOURLY
            spec.every = max(1, min(count, 24))
        elif unit == "day":
            spec.recurrence, spec.every = Recurrence.DAILY, max(1, min(count, 52))
        elif unit == "week":
            spec.recurrence, spec.every = Recurrence.WEEKLY, max(1, min(count, 52))
            spec.weekdays = [(now or datetime.now(timezone.utc)).astimezone(spec.zone).weekday()]
        else:
            spec.recurrence, spec.every = Recurrence.MONTHLY, max(1, min(count, 12))
        return spec

    monthly = _MONTHLY.search(text)
    if monthly:
        spec.recurrence = Recurrence.MONTHLY
        word = (monthly.group(1) or "").lower()
        if word in ("first", "1st"):
            spec.day_of_month = 1
        elif word == "last":
            spec.day_of_month = 31
        elif monthly.group(2):
            spec.day_of_month = max(1, min(int(monthly.group(2)), 31))
        return spec

    if _WORKDAYS.search(text) or _WEEKEND.search(text):
        spec.recurrence = Recurrence.WEEKLY
        spec.weekdays = [5, 6] if _WEEKEND.search(text) else [0, 1, 2, 3, 4]
        return spec

    weekday = _WEEKDAY.search(text)
    if weekday or _WEEKLY.search(text):
        spec.recurrence = Recurrence.WEEKLY
        days = {WEEKDAYS.index(m.group(1).lower().rstrip("s")) for m in _WEEKDAY.finditer(text)}
        spec.weekdays = sorted(days) or [(now or datetime.now(timezone.utc)).astimezone(spec.zone).weekday()]
        return spec

    if _HOURLY.search(text):
        spec.recurrence = Recurrence.HOURLY
        return spec
    if _DAILY.search(text):
        spec.recurrence = Recurrence.DAILY
        return spec
    if has_recurring_intent(text):
        # Clearly recurring, no cadence said: daily is the gentlest reading.
        spec.recurrence = Recurrence.DAILY
        return spec
    return None


def wants_monitoring(text: str) -> bool:
    """Did the person ask to hear only when something is true?"""
    return bool(_MONITOR.search(text or ""))


def strip_schedule_words(text: str) -> str:
    """The instruction without the timing, so the provider gets the work itself."""
    cleaned = text
    for pattern in (_EVERY_N, _EVERY_OTHER, _MONTHLY, _WORKDAYS, _WEEKEND, _WEEKLY, _HOURLY, _DAILY, _WEEKDAY):
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", " ", cleaned, flags=re.I)
    # The removals above leave gaps: "every weekday morning" is now "every  morning".
    # Close them up so the phrases below still match as phrases.
    cleaned = " ".join(cleaned.split())
    for word in TIME_WORDS:
        cleaned = re.sub(rf"\b(in the |every |each )?{word}\b", " ", cleaned, flags=re.I)
    # Words left dangling once the timing has gone: "... changes every".
    cleaned = re.sub(r"\b(every|each|on|at|in the)\s*(?=$|[,.;:])", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"^\s*(and|then|also|please)\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"^\s*[,.;:]+\s*|\s*[,.;:]+\s*$", " ", cleaned)
    return " ".join(cleaned.split()).strip(" ,.;:") or text
