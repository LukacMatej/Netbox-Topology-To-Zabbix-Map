from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZabbixHost:
    hostid: str
    host: str
    name: str
    # -1 disabled (Zabbix default), 0 manual, 1 automatic.
    inventory_mode: int = -1
    inventory_type: str = ""
