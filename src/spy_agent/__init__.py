"""Shadow-only SPY options research package."""

from .adapter import AdapterError, normalize_bars, normalize_option_chain
from .events import (
    EventConfigError,
    HighImpactEvent,
    evaluate_event_gate,
    load_high_impact_events,
)
from .pipeline import evaluate_snapshot, save_sanitized_snapshot

__all__ = [
    "AdapterError",
    "EventConfigError",
    "HighImpactEvent",
    "evaluate_event_gate",
    "evaluate_snapshot",
    "load_high_impact_events",
    "normalize_bars",
    "normalize_option_chain",
    "save_sanitized_snapshot",
]
