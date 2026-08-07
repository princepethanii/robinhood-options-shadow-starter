from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

CHICAGO = ZoneInfo("America/Chicago")
BLACKOUT_BEFORE = timedelta(minutes=10)
BLACKOUT_AFTER = timedelta(minutes=15)
DEFAULT_EVENT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "high-impact-events.yaml"
)

_ROOT_FIELDS = frozenset({"timezone", "events"})
_EVENT_FIELDS = frozenset(
    {"date", "timestamp", "event_name", "source", "risk_level"}
)
_CLOCK_PATTERN = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_YAML_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"


class _EventConfigLoader(yaml.SafeLoader):
    """Safe YAML loader that leaves calendar dates as strings for validation."""

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found a duplicate key",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


_EventConfigLoader.yaml_implicit_resolvers = {
    key: [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag != _YAML_TIMESTAMP_TAG
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


class EventConfigError(ValueError):
    """A fail-closed validation error in the curated event calendar."""

    def __init__(self, issues: Sequence[str]) -> None:
        normalized = tuple(str(issue) for issue in issues if str(issue))
        if not normalized:
            normalized = ("unknown validation error",)
        self.issues = normalized
        super().__init__("; ".join(normalized))


@dataclass(frozen=True)
class HighImpactEvent:
    event_date: date
    scheduled_at_ct: datetime
    event_name: str
    source: str
    risk_level: str

    def __post_init__(self) -> None:
        if isinstance(self.event_date, datetime) or not isinstance(self.event_date, date):
            raise TypeError("event_date must be a calendar date")
        if (
            self.scheduled_at_ct.tzinfo is None
            or self.scheduled_at_ct.utcoffset() is None
        ):
            raise ValueError("scheduled_at_ct must be timezone-aware")
        if getattr(self.scheduled_at_ct.tzinfo, "key", None) != "America/Chicago":
            raise ValueError("scheduled_at_ct must use America/Chicago")
        if self.event_date != self.scheduled_at_ct.date():
            raise ValueError("event_date must match scheduled_at_ct in America/Chicago")
        if self.risk_level != "high":
            raise ValueError("risk_level must be 'high'")
        for field, value in (("event_name", self.event_name), ("source", self.source)):
            if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
                raise ValueError(f"{field} must be a non-empty single-line string")

    @property
    def blackout_start_ct(self) -> datetime:
        start = self.scheduled_at_ct.astimezone(UTC) - BLACKOUT_BEFORE
        return start.astimezone(CHICAGO)

    @property
    def blackout_end_ct(self) -> datetime:
        end = self.scheduled_at_ct.astimezone(UTC) + BLACKOUT_AFTER
        return end.astimezone(CHICAGO)


@dataclass(frozen=True)
class EventGateResult:
    evaluated_at_ct: datetime
    total_events: int
    blocked_events: tuple[HighImpactEvent, ...]

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_events)


def load_high_impact_events(
    path: str | Path = DEFAULT_EVENT_CONFIG_PATH,
) -> tuple[HighImpactEvent, ...]:
    """Load and strictly validate the local, manually curated event calendar.

    ``source`` is retained only as inert provenance. This module performs no
    network access and never dereferences or fetches a source value.
    """

    if isinstance(path, bool) or not isinstance(path, (str, Path)):
        raise EventConfigError(
            ["high-impact event config path must be a filesystem path"]
        )
    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EventConfigError(
            ["high-impact event config file is missing"]
        ) from exc
    except OSError as exc:
        raise EventConfigError(
            [f"high-impact event config file could not be read ({type(exc).__name__})"]
        ) from exc
    except UnicodeError as exc:
        raise EventConfigError(
            ["high-impact event config must be valid UTF-8 text"]
        ) from exc

    try:
        payload = yaml.load(text, Loader=_EventConfigLoader)
    except (yaml.YAMLError, ValueError) as exc:
        raise EventConfigError(
            ["high-impact event config is not valid YAML"]
        ) from exc

    if not isinstance(payload, Mapping):
        raise EventConfigError(
            ["high-impact event config root must be a mapping"]
        )

    issues: list[str] = []
    root_keys = set(payload)
    missing_root = _ROOT_FIELDS - root_keys
    unexpected_root = root_keys - _ROOT_FIELDS
    for field in sorted(missing_root):
        issues.append(f"high-impact event config is missing root field {field!r}")
    for field in sorted(unexpected_root, key=str):
        issues.append(f"high-impact event config has unexpected root field {field!r}")

    timezone_value = payload.get("timezone")
    if timezone_value != "America/Chicago":
        issues.append("high-impact event config timezone must be 'America/Chicago'")

    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        issues.append("high-impact event config field 'events' must be a list")
        raise EventConfigError(issues)

    events: list[HighImpactEvent] = []
    for index, raw_event in enumerate(raw_events):
        parsed = _parse_event(raw_event, index, issues)
        if parsed is not None:
            events.append(parsed)

    if issues:
        raise EventConfigError(issues)

    return tuple(
        sorted(
            events,
            key=lambda event: (
                event.scheduled_at_ct,
                event.event_name,
                event.source,
            ),
        )
    )


def evaluate_event_gate(
    events: Sequence[HighImpactEvent],
    now: datetime,
) -> EventGateResult:
    """Evaluate the inclusive -10/+15 minute entry blackout in Chicago time."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("event gate evaluation time must be timezone-aware")
    evaluated_at = now.astimezone(CHICAGO)
    if not math.isfinite(evaluated_at.timestamp()):
        raise ValueError("event gate evaluation time must be finite")

    evaluated_at_utc = evaluated_at.astimezone(UTC)
    blocked_events = tuple(
        event
        for event in events
        if event.blackout_start_ct.astimezone(UTC)
        <= evaluated_at_utc
        <= event.blackout_end_ct.astimezone(UTC)
    )
    return EventGateResult(
        evaluated_at_ct=evaluated_at,
        total_events=len(events),
        blocked_events=blocked_events,
    )


def _parse_event(
    raw_event: Any,
    index: int,
    issues: list[str],
) -> HighImpactEvent | None:
    label = f"events[{index}]"
    if not isinstance(raw_event, Mapping):
        issues.append(f"{label} must be a mapping")
        return None

    event_keys = set(raw_event)
    missing = _EVENT_FIELDS - event_keys
    unexpected = event_keys - _EVENT_FIELDS
    for field in sorted(missing):
        issues.append(f"{label} is missing required field {field!r}")
    for field in sorted(unexpected, key=str):
        issues.append(f"{label} has unexpected field {field!r}")
    if missing:
        return None

    event_date = _parse_date(raw_event.get("date"), label, issues)
    event_time = _parse_time(raw_event.get("timestamp"), label, issues)
    event_name = _parse_nonempty_text(
        raw_event.get("event_name"), "event_name", label, issues
    )
    source = _parse_nonempty_text(raw_event.get("source"), "source", label, issues)

    risk_level_value = raw_event.get("risk_level")
    risk_level: str | None = None
    if risk_level_value != "high":
        issues.append(f"{label}.risk_level must be 'high'")
    else:
        risk_level = risk_level_value

    if (
        event_date is None
        or event_time is None
        or event_name is None
        or source is None
        or risk_level is None
    ):
        return None

    scheduled_at = _localize_strict(event_date, event_time, label, issues)
    if scheduled_at is None:
        return None
    return HighImpactEvent(
        event_date=event_date,
        scheduled_at_ct=scheduled_at,
        event_name=event_name,
        source=source,
        risk_level=risk_level,
    )


def _parse_date(value: Any, label: str, issues: list[str]) -> date | None:
    if isinstance(value, (datetime, bool)):
        issues.append(f"{label}.date must be a YYYY-MM-DD calendar date")
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value) is None:
        issues.append(f"{label}.date must be a YYYY-MM-DD calendar date")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        issues.append(f"{label}.date is not a valid calendar date")
        return None


def _parse_time(value: Any, label: str, issues: list[str]) -> time | None:
    if not isinstance(value, str) or _CLOCK_PATTERN.fullmatch(value) is None:
        issues.append(
            f"{label}.timestamp must be a quoted HH:MM America/Chicago time"
        )
        return None
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


def _parse_nonempty_text(
    value: Any,
    field: str,
    label: str,
    issues: list[str],
) -> str | None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\n" in value
        or "\r" in value
    ):
        issues.append(f"{label}.{field} must be a non-empty single-line string")
        return None
    return value.strip()


def _localize_strict(
    event_date: date,
    event_time: time,
    label: str,
    issues: list[str],
) -> datetime | None:
    naive = datetime.combine(event_date, event_time)
    fold_zero = naive.replace(tzinfo=CHICAGO, fold=0)
    fold_one = naive.replace(tzinfo=CHICAGO, fold=1)
    zero_round_trip = fold_zero.astimezone(UTC).astimezone(CHICAGO).replace(tzinfo=None)
    one_round_trip = fold_one.astimezone(UTC).astimezone(CHICAGO).replace(tzinfo=None)
    zero_valid = zero_round_trip == naive
    one_valid = one_round_trip == naive

    if not zero_valid and not one_valid:
        issues.append(f"{label}.timestamp is nonexistent in America/Chicago")
        return None
    if zero_valid and one_valid and fold_zero.utcoffset() != fold_one.utcoffset():
        issues.append(f"{label}.timestamp is ambiguous in America/Chicago")
        return None
    return fold_zero if zero_valid else fold_one
