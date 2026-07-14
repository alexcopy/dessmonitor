"""Passive schedule profile model for dessmonitor.

Defines frozen dataclasses representing schedule profiles for device loads:
  - ScheduleWindow: a single time-of-day control window.
  - ScheduleProfile: a named profile with windows, day/week, season.
  - LoadScheduleProfile: associates a load with its schedule profiles.

The model is passive data only — it does not evaluate real-time schedules,
read current time, wire into runtime components, switch devices, or execute
any scheduler loop. Schedule evaluation is deferred to the future decision
engine (PR 0018).

This module uses only:
  - Python standard library (dataclasses, typing)
  - app.control.energy_policy types: TimeOfDay, Season
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from app.control.energy_policy import (
    Season,
    TimeOfDay,
)

# ---------------------------------------------------------------------------
# Default configuration constant
# ---------------------------------------------------------------------------

DEFAULT_CHECK_INTERVAL_SECONDS: int = 300

# ---------------------------------------------------------------------------
# Frozen dataclasses — passive data only, no evaluation logic
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleWindow:
    """A single time-of-day window within a schedule profile.

    Passive data only — no time evaluation, no scheduling, no hardware calls.
    """
    window_id: str
    time_of_day: TimeOfDay
    check_interval_seconds: int = DEFAULT_CHECK_INTERVAL_SECONDS
    days_of_week: Tuple[int, ...] = field(default_factory=tuple)
    enabled: bool = True


@dataclass(frozen=True)
class ScheduleProfile:
    """A named schedule profile with windows, season, and description.

    Passive data only — no time evaluation, no scheduling, no hardware calls.
    """
    profile_id: str
    season: Optional[Season] = None
    windows: Tuple[ScheduleWindow, ...] = field(default_factory=tuple)
    enabled: bool = True
    description: str = ""


@dataclass(frozen=True)
class LoadScheduleProfile:
    """Associates a device load with one or more schedule profiles.

    Passive data only — no time evaluation, no scheduling, no hardware calls.
    """
    load_id: str
    profiles: Tuple[ScheduleProfile, ...] = field(default_factory=tuple)
    default_check_interval_seconds: int = DEFAULT_CHECK_INTERVAL_SECONDS
    enabled: bool = True
