from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerChoice:
    triggerid: str
    description: str


@dataclass(frozen=True)
class CableTriggerContext:
    cable_id: str
    device_a: str
    device_b: str
    selected_triggers: tuple[str, ...]
    available_triggers: tuple[TriggerChoice, ...]
