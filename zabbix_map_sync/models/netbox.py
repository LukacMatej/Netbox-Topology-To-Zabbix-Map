from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DevicePositionRecord:
    device_id: str
    positions_by_map: dict[str, dict]
