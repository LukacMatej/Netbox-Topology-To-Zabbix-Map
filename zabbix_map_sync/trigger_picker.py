from __future__ import annotations

import html
import logging
from dataclasses import dataclass

from .config import Settings
from .netbox import NetBoxClient
from .ui import render_banner, render_page
from .zabbix import ZabbixClient


logger = logging.getLogger(__name__)

TRIGGER_CUSTOM_FIELD = "zabbix_triggers"


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


def _device_name(device: dict | None) -> str:
    if not isinstance(device, dict):
        return ""
    for key in ("name", "display"):
        value = str(device.get(key, "")).strip()
        if value:
            return value
    return ""


def load_cable_trigger_context(netbox: NetBoxClient, zabbix: ZabbixClient, cable_id: str) -> CableTriggerContext:
    cable = netbox.get_cable(cable_id)

    device_pair = netbox.resolve_cable_device_pair(cable)
    if not device_pair:
        raise ValueError(f"Could not resolve both endpoint devices for cable_id={cable_id}")

    devices_by_id = netbox.fetch_devices_by_ids(set(device_pair))
    device_a_name = _device_name(devices_by_id.get(device_pair[0]))
    device_b_name = _device_name(devices_by_id.get(device_pair[1]))

    hosts = zabbix.get_hosts_by_names([name for name in (device_a_name, device_b_name) if name])
    hostids = sorted({host.hostid for host in hosts.values()})
    if not hostids:
        logger.warning(
            "No Zabbix hosts resolved for cable_id=%s devices=%s,%s",
            cable_id,
            device_a_name,
            device_b_name,
        )

    raw_triggers = zabbix.list_triggers_for_hosts(hostids)
    available_triggers = tuple(
        TriggerChoice(triggerid=str(item.get("triggerid", "")), description=str(item.get("description", "")))
        for item in raw_triggers
    )

    logger.info(
        "Loaded trigger picker context cable_id=%s devices=%s,%s available_triggers=%s",
        cable_id,
        device_a_name,
        device_b_name,
        len(available_triggers),
    )

    return CableTriggerContext(
        cable_id=str(cable_id),
        device_a=device_a_name,
        device_b=device_b_name,
        selected_triggers=netbox.get_cable_trigger_names(cable),
        available_triggers=available_triggers,
    )


def save_cable_trigger_selection(netbox: NetBoxClient, cable_id: str, trigger_names: list[str]) -> None:
    cleaned = [name.strip() for name in trigger_names if name.strip()]
    logger.info("Saving cable trigger selection cable_id=%s trigger_names=%s", cable_id, cleaned)
    netbox.set_cable_custom_field(cable_id, TRIGGER_CUSTOM_FIELD, cleaned)


def render_trigger_picker_html(context: CableTriggerContext, saved: bool = False) -> str:
    selected = set(context.selected_triggers)
    rows: list[str] = []
    for choice in context.available_triggers:
        checked = " checked" if choice.description in selected else ""
        rows.append(
            "<label class='trigger'>"
            f"<input type='checkbox' name='trigger' value=\"{html.escape(choice.description)}\"{checked}>"
            f"<span>{html.escape(choice.description)}</span></label>"
        )
    list_html = (
        "".join(rows) if rows else "<p class='empty'>No Zabbix triggers found for these two hosts.</p>"
    )

    banner_html = render_banner("Saved.") if saved else ""

    body = (
        f"<h1>Link triggers</h1>"
        f"<p class='subtitle'>{html.escape(context.device_a)} &harr; {html.escape(context.device_b)}</p>"
        f"{banner_html}"
        f"<form method='post' action='/cables/{html.escape(context.cable_id)}/triggers'>"
        "<div class='card'>"
        f"<div class='trigger-list'>{list_html}</div>"
        "<button type='submit'>Save</button>"
        "</div>"
        "</form>"
    )
    return render_page(f"Link triggers &ndash; {html.escape(context.device_a)} / {html.escape(context.device_b)}", body)


def _build_netbox_client(settings: Settings) -> NetBoxClient:
    return NetBoxClient(base_url=settings.netbox_url, token=settings.netbox_token)


def _build_zabbix_client(settings: Settings) -> ZabbixClient:
    zabbix = ZabbixClient(
        api_url=settings.zabbix_url,
        user=settings.zabbix_user,
        password=settings.zabbix_password,
        api_token=settings.zabbix_token,
    )
    zabbix.login()
    return zabbix


def get_cable_trigger_page(settings: Settings, cable_id: str, saved: bool = False) -> str:
    netbox = _build_netbox_client(settings)
    zabbix = _build_zabbix_client(settings)
    context = load_cable_trigger_context(netbox, zabbix, cable_id)
    return render_trigger_picker_html(context, saved=saved)


def apply_cable_trigger_selection(settings: Settings, cable_id: str, trigger_names: list[str]) -> None:
    netbox = _build_netbox_client(settings)
    save_cable_trigger_selection(netbox, cable_id, trigger_names)
