"""When work should happen: reading a person's words, and counting time properly.

Pure functions with a fixed clock: no machine timezone, no real waiting.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.automations.schedule import (
    Recurrence,
    ScheduleSpec,
    has_recurring_intent,
    next_occurrence,
    parse_schedule,
    strip_schedule_words,
    wants_monitoring,
)

LONDON = ZoneInfo("Europe/London")
# A Wednesday, 10:30 London time.
NOW = datetime(2026, 3, 4, 10, 30, tzinfo=LONDON).astimezone(timezone.utc)


def london(text: str, now: datetime = NOW) -> ScheduleSpec | None:
    return parse_schedule(text, tz="Europe/London", now=now)


@pytest.mark.parametrize(
    ("text", "recurrence", "described"),
    [
        ("Research competitor changes every Monday morning.", Recurrence.WEEKLY, "Every Monday · 09:00"),
        ("Research X every day at 09:00.", Recurrence.DAILY, "Every day · 09:00"),
        ("Run this report on the first day of every month.", Recurrence.MONTHLY, "The 1st of every month · 09:00"),
        ("Check this every hour.", Recurrence.HOURLY, "Every hour"),
        ("Check every 15 minutes.", Recurrence.MINUTES, "Every 15 minutes"),
        ("Summarise the news every evening.", Recurrence.DAILY, "Every day · 18:00"),
        ("Every Friday at 4pm, check the rota.", Recurrence.WEEKLY, "Every Friday · 16:00"),
        ("Check the last day of every month.", Recurrence.MONTHLY, "The last day of every month · 09:00"),
        ("Do this every other Monday.", Recurrence.WEEKLY, "Every 2 weeks on Monday · 09:00"),
        ("Check every 3 days at 7am.", Recurrence.DAILY, "Every 3 days · 07:00"),
        ("Check the rota every weekday morning.", Recurrence.WEEKLY, "Every weekday · 09:00"),
        ("Summarise the inbox on weekdays at 8am.", Recurrence.WEEKLY, "Every weekday · 08:00"),
        ("Check the shop Mon-Fri at 7am.", Recurrence.WEEKLY, "Every weekday · 07:00"),
        ("Tidy the notes every weekend.", Recurrence.WEEKLY, "Every weekend day · 09:00"),
    ],
)
def test_a_person_never_types_cron(text, recurrence, described):
    spec = london(text)
    assert spec is not None and spec.recurrence == recurrence
    assert spec.describe() == described
    assert spec.timezone == "Europe/London"


def test_a_one_off_request_is_not_a_schedule():
    for text in ("Research competitors in event management software.", "Compare three note-taking apps.", "Fix the spacing on Recent."):
        assert london(text) is None and not has_recurring_intent(text)


def test_monitoring_is_recognised_from_the_words():
    assert wants_monitoring("Check this every day and tell me only when something changes.")
    assert wants_monitoring("Watch for a competitor launching room allocation.")
    assert not wants_monitoring("Research competitors every Monday.")


def test_the_instruction_keeps_the_work_and_loses_the_timing():
    assert strip_schedule_words("Research competitor changes every Monday morning") == "Research competitor changes"
    assert strip_schedule_words("Every day at 09:00, summarise my inbox") == "summarise my inbox"
    assert strip_schedule_words("Research competitors") == "Research competitors"


# ----------------------------------------------------------------------------- counting time

def test_the_next_turn_is_the_hour_the_person_meant():
    spec = london("every Monday morning")
    due = next_occurrence(spec, NOW)
    local = due.astimezone(LONDON)
    assert local.weekday() == 0 and local.hour == 9 and local.minute == 0
    assert local.date() == datetime(2026, 3, 9).date()  # the coming Monday


def test_daily_rolls_to_tomorrow_once_today_has_passed():
    morning = datetime(2026, 3, 4, 8, 0, tzinfo=LONDON).astimezone(timezone.utc)
    spec = london("every day at 09:00", now=morning)
    assert next_occurrence(spec, morning).astimezone(LONDON).date() == datetime(2026, 3, 4).date()
    after = datetime(2026, 3, 4, 9, 30, tzinfo=LONDON).astimezone(timezone.utc)
    assert next_occurrence(spec, after).astimezone(LONDON).date() == datetime(2026, 3, 5).date()


def test_the_clock_follows_the_named_timezone_through_daylight_saving():
    # British clocks go forward on 29 March 2026. A 09:00 job stays at 09:00
    # local, which is a different hour in UTC either side of the change.
    spec = ScheduleSpec(recurrence=Recurrence.DAILY, at=time(9, 0), timezone="Europe/London")
    before = next_occurrence(spec, datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc))
    after = next_occurrence(spec, datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc))
    assert before.astimezone(LONDON).hour == 9 and after.astimezone(LONDON).hour == 9
    assert before.hour == 9 and after.hour == 8  # UTC moved, the person's morning did not
    # And a schedule in another zone is unaffected by the machine's own.
    ny = ScheduleSpec(recurrence=Recurrence.DAILY, at=time(9, 0), timezone="America/New_York")
    due = next_occurrence(ny, datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc))
    assert due.astimezone(ZoneInfo("America/New_York")).hour == 9


def test_an_hour_that_does_not_exist_still_runs():
    # 01:30 does not happen on the morning the clocks go forward.
    spec = ScheduleSpec(recurrence=Recurrence.DAILY, at=time(1, 30), timezone="Europe/London")
    due = next_occurrence(spec, datetime(2026, 3, 29, 0, 30, tzinfo=timezone.utc))
    assert due is not None and due > datetime(2026, 3, 29, 0, 30, tzinfo=timezone.utc)


def test_monthly_handles_short_months_and_the_last_day():
    first = ScheduleSpec(recurrence=Recurrence.MONTHLY, day_of_month=1, timezone="UTC")
    due = next_occurrence(first, datetime(2026, 1, 15, tzinfo=timezone.utc))
    assert due.date() == datetime(2026, 2, 1).date()
    last = ScheduleSpec(recurrence=Recurrence.MONTHLY, day_of_month=31, timezone="UTC")
    due = next_occurrence(last, datetime(2026, 2, 10, tzinfo=timezone.utc))
    assert due.date() == datetime(2026, 2, 28).date()  # February, not the 31st
    thirtieth = ScheduleSpec(recurrence=Recurrence.MONTHLY, day_of_month=30, timezone="UTC")
    assert next_occurrence(thirtieth, datetime(2026, 2, 1, tzinfo=timezone.utc)).date() == datetime(2026, 2, 28).date()


def test_a_schedule_can_end():
    spec = ScheduleSpec(recurrence=Recurrence.DAILY, timezone="UTC", end_at=datetime(2026, 3, 5, tzinfo=timezone.utc))
    assert next_occurrence(spec, datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc)) is not None
    assert next_occurrence(spec, datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc)) is None


def test_advanced_details_can_show_the_recurrence_but_nobody_has_to_read_it():
    spec = london("every Monday morning")
    assert spec.canonical() == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO;BYHOUR=9;BYMINUTE=0"
    assert "FREQ" not in spec.describe()


def test_weekday_means_monday_to_friday_and_skips_the_weekend():
    spec = london("check the rota every weekday morning")
    assert spec.weekdays == [0, 1, 2, 3, 4]
    friday = datetime(2026, 3, 6, 12, 0, tzinfo=LONDON).astimezone(timezone.utc)
    due = next_occurrence(spec, friday)
    assert due.astimezone(LONDON).strftime("%A %H:%M") == "Monday 09:00"  # not Saturday
    weekend = london("tidy the notes every weekend")
    assert weekend.weekdays == [5, 6]
    assert next_occurrence(weekend, friday).astimezone(LONDON).strftime("%A") == "Saturday"
    assert spec.canonical() == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=0"
    assert strip_schedule_words("check the rota every weekday morning") == "check the rota"
    # The timing may sit in the middle of the sentence, with nothing left dangling.
    assert strip_schedule_words("every weekday morning check the rota") == "check the rota"
    assert strip_schedule_words("every weekend tidy the notes") == "tidy the notes"
