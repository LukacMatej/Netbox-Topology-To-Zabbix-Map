from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Any, Literal

import requests


class ZabbixAPIError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZabbixHost:
    hostid: str
    host: str
    name: str


class ZabbixClient:
    def __init__(
        self,
        api_url: str,
        user: str,
        password: str,
        api_token: str = "",
        timeout: int = 30,
    ) -> None:
        self.api_url: str = api_url
        self.user: str = user
        self.password: str = password
        self.timeout: int = timeout
        self._request_id: itertools.count[int] = itertools.count(1)
        self._auth: str | None = api_token or None
        self._auth_mode: Literal['bearer'] | Literal['legacy'] = "bearer" if api_token else "legacy"

    def login(self) -> None:
        if self._auth_mode == "bearer" and self._auth:
            logger.debug("Using bearer token authentication for Zabbix")
            return
        logger.debug("Logging into Zabbix with legacy user.login")
        token = self._rpc("user.login", {"username": self.user, "password": self.password}, auth=False)
        if not isinstance(token, str):
            raise ZabbixAPIError("Could not authenticate in Zabbix")
        self._auth = token
        self._auth_mode = "legacy"

    def _rpc(self, method: str, params: Any, auth: bool = True, _retry: bool = True):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": next(self._request_id),
        }
        headers: dict[str, str] = {}
        if auth:
            if not self._auth:
                raise ZabbixAPIError("Zabbix client is not authenticated")
            if self._auth_mode == "legacy":
                payload["auth"] = self._auth
            else:
                headers["Authorization"] = f"Bearer {self._auth}"

        logger.debug("Zabbix RPC method=%s auth=%s", method, auth)

        response = requests.post(self.api_url, json=payload, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        result = response.json()

        if "error" in result:
            error = result["error"]
            error_text = str(error)
            if (
                auth
                and _retry
                and self._auth
                and self._auth_mode == "legacy"
                and 'unexpected parameter "auth"' in error_text
            ):
                logger.debug("Zabbix rejected legacy auth payload; retrying with bearer mode")
                self._auth_mode = "bearer"
                return self._rpc(method, params, auth=auth, _retry=False)
            raise ZabbixAPIError(f"Zabbix API error in {method}: {error}")

        if "result" not in result:
            raise ZabbixAPIError(f"Malformed Zabbix API response for {method}")
        return result["result"]

    def get_hosts_by_names(self, names: list[str]) -> dict[str, ZabbixHost]:
        if not names:
            return {}

        logger.debug("Fetching Zabbix hosts for %s topology labels", len(names))

        result = self._rpc(
            "host.get",
            {
                "output": ["hostid", "host", "name"],
                "filter": {"host": names},
            },
        )

        hosts: dict[str, ZabbixHost] = {}
        for item in result:
            host = ZabbixHost(hostid=item["hostid"], host=item["host"], name=item.get("name", item["host"]))
            hosts[item["host"]] = host
            hosts[item.get("name", item["host"])] = host
        logger.debug("Resolved %s Zabbix host lookup entries", len(hosts))
        return hosts

    def get_map_by_name(self, map_name: str) -> dict | None:
        logger.debug("Looking up existing map name=%s", map_name)
        maps = self._rpc(
            "map.get",
            {
                "output": "extend",
                "filter": {"name": [map_name]},
                "selectSelements": "extend",
                "selectLinks": "extend",
            },
        )
        if not maps:
            logger.debug("Map name=%s does not exist", map_name)
            return None
        logger.debug("Found existing map name=%s sysmapid=%s", map_name, maps[0].get("sysmapid"))
        return maps[0]

    def find_trigger_id(self, hostids: list[str], trigger_name: str, match: str = "auto") -> str | None:
        if not hostids or not trigger_name.strip():
            return None

        logger.debug(
            "Searching Zabbix trigger hostids=%s trigger_name=%s match=%s",
            hostids,
            trigger_name,
            match,
        )

        if match in {"exact", "auto"}:
            exact_matches = self._rpc(
                "trigger.get",
                {
                    "output": ["triggerid", "description", "status", "value"],
                    "hostids": hostids,
                    "filter": {"description": [trigger_name]},
                    "sortfield": "priority",
                    "sortorder": "DESC",
                    "limit": 1,
                },
            )
            if exact_matches:
                logger.debug(
                    "Exact trigger match found trigger_name=%s triggerid=%s",
                    trigger_name,
                    exact_matches[0].get("triggerid"),
                )
                return exact_matches[0].get("triggerid")

        if match in {"contains", "auto"}:
            partial_matches = self._rpc(
                "trigger.get",
                {
                    "output": ["triggerid", "description", "status", "value"],
                    "hostids": hostids,
                    "search": {"description": trigger_name},
                    "searchWildcardsEnabled": True,
                    "sortfield": "priority",
                    "sortorder": "DESC",
                    "limit": 1,
                },
            )
            if partial_matches:
                logger.debug(
                    "Partial trigger match found trigger_name=%s triggerid=%s description=%s",
                    trigger_name,
                    partial_matches[0].get("triggerid"),
                    partial_matches[0].get("description"),
                )
                return partial_matches[0].get("triggerid")
        logger.debug("No Zabbix trigger matched trigger_name=%s hostids=%s", trigger_name, hostids)
        return None

    def create_map(self, payload: dict) -> dict:
        logger.info(
            "Creating Zabbix map name=%s selements=%s links=%s",
            payload.get("name"),
            len(payload.get("selements", []) or []),
            len(payload.get("links", []) or []),
        )
        return self._rpc("map.create", payload)

    def delete_map(self, mapid: str) -> dict:
        return self._rpc("map.delete", [mapid])

    def update_map(self, mapid: str, payload: dict) -> dict:
        update_payload = {"sysmapid": mapid, **payload}
        logger.info(
            "Updating Zabbix map sysmapid=%s name=%s selements=%s links=%s",
            mapid,
            payload.get("name"),
            len(payload.get("selements", []) or []),
            len(payload.get("links", []) or []),
        )
        return self._rpc("map.update", update_payload)
