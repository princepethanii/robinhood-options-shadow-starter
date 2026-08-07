from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from spy_agent.events import (
    BLACKOUT_AFTER,
    BLACKOUT_BEFORE,
    EventConfigError,
    HighImpactEvent,
    evaluate_event_gate,
    load_high_impact_events,
)

CHICAGO = ZoneInfo("America/Chicago")
EVENT_AT = datetime(2026, 8, 6, 9, 0, tzinfo=CHICAGO)


def write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "high-impact-events.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def valid_event_yaml(*, timestamp: str = "09:00") -> str:
    return f"""\
timezone: America/Chicago
events:
  - date: 2026-08-06
    timestamp: "{timestamp}"
    event_name: "Employment report"
    source: "Official statistical agency"
    risk_level: high
"""


def event_at(timestamp: datetime = EVENT_AT, name: str = "Employment report") -> HighImpactEvent:
    return HighImpactEvent(
        event_date=timestamp.date(),
        scheduled_at_ct=timestamp,
        event_name=name,
        source="Official statistical agency",
        risk_level="high",
    )


def test_checked_in_calendar_is_explicitly_valid_and_empty() -> None:
    assert load_high_impact_events() == ()


def test_loader_accepts_yaml_date_and_sorts_events_chronologically(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """\
timezone: America/Chicago
events:
  - date: "2026-08-06"
    timestamp: "10:00"
    event_name: "Later event"
    source: "Official source B"
    risk_level: high
  - date: 2026-08-06
    timestamp: "09:00"
    event_name: "Earlier event"
    source: "Official source A"
    risk_level: high
""",
    )

    events = load_high_impact_events(path)

    assert [event.event_name for event in events] == ["Earlier event", "Later event"]
    assert events[0].event_date == date(2026, 8, 6)
    assert events[0].scheduled_at_ct.isoformat() == "2026-08-06T09:00:00-05:00"
    assert events[0].risk_level == "high"


@pytest.mark.parametrize(
    ("offset", "blocked"),
    [
        (-BLACKOUT_BEFORE - timedelta(microseconds=1), False),
        (-BLACKOUT_BEFORE, True),
        (timedelta(0), True),
        (BLACKOUT_AFTER, True),
        (BLACKOUT_AFTER + timedelta(microseconds=1), False),
    ],
)
def test_gate_uses_inclusive_ten_before_fifteen_after_boundaries(
    offset: timedelta,
    blocked: bool,
) -> None:
    result = evaluate_event_gate((event_at(),), EVENT_AT + offset)

    assert result.blocked is blocked


def test_gate_converts_utc_to_chicago_and_reports_overlapping_events() -> None:
    events = (event_at(name="Event A"), event_at(name="Event B"))

    result = evaluate_event_gate(events, datetime(2026, 8, 6, 14, 0, tzinfo=UTC))

    assert result.evaluated_at_ct == EVENT_AT
    assert [event.event_name for event in result.blocked_events] == ["Event A", "Event B"]


def test_gate_handles_blackout_across_calendar_date_boundary() -> None:
    midnight_event = event_at(datetime(2026, 8, 7, 0, 5, tzinfo=CHICAGO))

    result = evaluate_event_gate(
        (midnight_event,),
        datetime(2026, 8, 6, 23, 58, tzinfo=CHICAGO),
    )

    assert result.blocked is True


def test_gate_rejects_naive_evaluation_time() -> None:
    naive = EVENT_AT.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_event_gate((event_at(),), naive)


def test_event_record_rejects_non_chicago_timestamp() -> None:
    with pytest.raises(ValueError, match="must use America/Chicago"):
        event_at(datetime(2026, 8, 6, 14, 0, tzinfo=UTC))


def test_missing_file_is_a_precise_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(EventConfigError, match="file is missing"):
        load_high_impact_events(tmp_path / "missing.yaml")


def test_invalid_config_path_is_a_precise_configuration_error() -> None:
    with pytest.raises(EventConfigError, match="filesystem path"):
        load_high_impact_events(None)  # type: ignore[arg-type]


def test_non_utf8_file_is_a_precise_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "high-impact-events.yaml"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(EventConfigError, match="valid UTF-8"):
        load_high_impact_events(path)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("", "root must be a mapping"),
        ("events: [", "not valid YAML"),
        ("- events", "root must be a mapping"),
        ("timezone: America/Chicago\n", "missing root field 'events'"),
        (
            "timezone: America/New_York\nevents: []\n",
            "timezone must be 'America/Chicago'",
        ),
        (
            "timezone: America/Chicago\nevents: {}\n",
            "field 'events' must be a list",
        ),
        (
            "timezone: America/Chicago\nevents: []\nunexpected: true\n",
            "unexpected root field 'unexpected'",
        ),
    ],
)
def test_loader_rejects_malformed_roots(
    tmp_path: Path,
    content: str,
    expected: str,
) -> None:
    path = write_config(tmp_path, content)

    with pytest.raises(EventConfigError, match=expected):
        load_high_impact_events(path)


@pytest.mark.parametrize(
    ("field", "replacement", "expected"),
    [
        ("date", "date: 2026-02-30", "not a valid calendar date"),
        ("timestamp", 'timestamp: "9:00"', "quoted HH:MM"),
        ("timestamp", 'timestamp: "09:00:00"', "quoted HH:MM"),
        ("timestamp", 'timestamp: "09:00-05:00"', "quoted HH:MM"),
        ("event_name", 'event_name: "  "', "non-empty single-line"),
        ("source", 'source: ""', "non-empty single-line"),
        ("risk_level", "risk_level: medium", "must be 'high'"),
    ],
)
def test_loader_rejects_invalid_event_fields(
    tmp_path: Path,
    field: str,
    replacement: str,
    expected: str,
) -> None:
    lines = valid_event_yaml().splitlines()
    updated_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(f"- {field}:"):
            updated_lines.append(f"  - {replacement}")
        elif stripped.startswith(f"{field}:"):
            updated_lines.append(f"    {replacement}")
        else:
            updated_lines.append(line)
    content = "\n".join(updated_lines)
    path = write_config(tmp_path, content)

    with pytest.raises(EventConfigError, match=expected):
        load_high_impact_events(path)


@pytest.mark.parametrize(
    "missing_field",
    ["date", "timestamp", "event_name", "source", "risk_level"],
)
def test_loader_rejects_each_missing_required_event_field(
    tmp_path: Path,
    missing_field: str,
) -> None:
    lines: list[str] = []
    for line in valid_event_yaml().splitlines():
        stripped = line.lstrip()
        if stripped.startswith(f"- {missing_field}:"):
            lines.append("  -")
        elif stripped.startswith(f"{missing_field}:"):
            continue
        else:
            lines.append(line)
    content = "\n".join(lines)
    path = write_config(tmp_path, content)

    with pytest.raises(EventConfigError, match=rf"missing required field '{missing_field}'"):
        load_high_impact_events(path)


def test_one_bad_event_invalidates_the_entire_calendar(tmp_path: Path) -> None:
    content = valid_event_yaml() + """\
  - date: 2026-08-07
    timestamp: "09:00"
    event_name: "Malformed event"
    source: "Official source"
    risk_level: medium
"""
    path = write_config(tmp_path, content)

    with pytest.raises(EventConfigError, match=r"events\[1\]\.risk_level"):
        load_high_impact_events(path)


@pytest.mark.parametrize(
    ("event_date", "timestamp", "expected"),
    [
        ("2026-03-08", "02:30", "nonexistent"),
        ("2026-11-01", "01:30", "ambiguous"),
    ],
)
def test_loader_rejects_dst_ambiguous_or_nonexistent_local_times(
    tmp_path: Path,
    event_date: str,
    timestamp: str,
    expected: str,
) -> None:
    content = valid_event_yaml(timestamp=timestamp).replace("2026-08-06", event_date)
    path = write_config(tmp_path, content)

    with pytest.raises(EventConfigError, match=expected):
        load_high_impact_events(path)


def test_loader_rejects_unexpected_event_field(tmp_path: Path) -> None:
    content = valid_event_yaml() + "    typo: true\n"
    path = write_config(tmp_path, content)

    with pytest.raises(EventConfigError, match="unexpected field 'typo'"):
        load_high_impact_events(path)


def test_loader_rejects_duplicate_fields(tmp_path: Path) -> None:
    content = valid_event_yaml().replace(
        '    timestamp: "09:00"',
        '    timestamp: "09:00"\n    timestamp: "09:05"',
    )
    path = write_config(tmp_path, content)

    with pytest.raises(EventConfigError, match="not valid YAML"):
        load_high_impact_events(path)
