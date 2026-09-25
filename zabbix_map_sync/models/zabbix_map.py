from __future__ import annotations

from dataclasses import dataclass

# Zabbix sysmap selement "elementtype" values
ELEMENT_TYPE_HOST = 0
ELEMENT_TYPE_IMAGE = 4

DEFAULT_LINK_DRAWTYPE = 0
DEFAULT_LINK_COLOR = "00AA00"
DEFAULT_LINK_TRIGGER_DRAWTYPE = "0"
DEFAULT_LINK_TRIGGER_COLOR = "FF0000"


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class MapLinkTrigger:
    triggerid: str
    drawtype: str = DEFAULT_LINK_TRIGGER_DRAWTYPE
    color: str = DEFAULT_LINK_TRIGGER_COLOR

    @classmethod
    def from_api(cls, raw) -> MapLinkTrigger | None:
        # Entries fetched back from Zabbix's map.get (selectLinks="extend") carry
        # read-only bookkeeping fields such as "linktriggerid"/"linkid" alongside
        # the real ones. Feeding those straight back into map.create/map.update
        # is what's actually accepted by some Zabbix versions but rejected by
        # others as "Wrong fields for map link.", so only the fields a link
        # trigger is ever written with are kept.
        if not isinstance(raw, dict):
            return None
        triggerid = str(raw.get("triggerid", "")).strip()
        if not triggerid:
            return None
        return cls(
            triggerid=triggerid,
            drawtype=str(raw.get("drawtype", DEFAULT_LINK_TRIGGER_DRAWTYPE)),
            color=str(raw.get("color", DEFAULT_LINK_TRIGGER_COLOR)),
        )

    def to_api(self) -> dict:
        return {"triggerid": self.triggerid, "drawtype": self.drawtype, "color": self.color}


@dataclass(frozen=True)
class MapElement:
    selementid: str
    elementtype: int
    label: str = ""
    # None only for elements read back from Zabbix with an unparseable position.
    x: int | None = None
    y: int | None = None
    iconid_off: str = ""
    hostid: str | None = None

    @property
    def is_host(self) -> bool:
        return self.hostid is not None

    @property
    def is_image(self) -> bool:
        return self.elementtype == ELEMENT_TYPE_IMAGE

    @classmethod
    def from_api(cls, raw: dict) -> MapElement:
        hostid = None
        elements = raw.get("elements")
        if isinstance(elements, list) and elements and isinstance(elements[0], dict):
            hostid = str(elements[0].get("hostid", "")).strip() or None

        elementtype = _to_int(raw.get("elementtype"))
        return cls(
            selementid=str(raw.get("selementid", "")).strip(),
            elementtype=ELEMENT_TYPE_HOST if elementtype is None else elementtype,
            label=str(raw.get("label", "")).strip(),
            x=_to_int(raw.get("x")),
            y=_to_int(raw.get("y")),
            iconid_off=str(raw.get("iconid_off", "")).strip(),
            hostid=hostid,
        )

    def to_api(self) -> dict:
        return {
            "selementid": self.selementid,
            "elementtype": self.elementtype,
            "elements": [{"hostid": self.hostid}] if self.hostid else [],
            "label": self.label,
            "iconid_off": self.iconid_off,
            "x": self.x,
            "y": self.y,
        }


@dataclass(frozen=True)
class MapLink:
    selementid1: str
    selementid2: str
    drawtype: int = DEFAULT_LINK_DRAWTYPE
    color: str = DEFAULT_LINK_COLOR
    indicator_type: int = 0
    linktriggers: tuple[MapLinkTrigger, ...] = ()

    @property
    def pair(self) -> tuple[str, str]:
        return tuple(sorted((self.selementid1, self.selementid2)))

    @classmethod
    def from_api(cls, raw: dict) -> MapLink:
        linktriggers = tuple(
            trigger
            for trigger in (MapLinkTrigger.from_api(entry) for entry in raw.get("linktriggers") or [])
            if trigger is not None
        )
        indicator_type = _to_int(raw.get("indicator_type"))
        if indicator_type is None:
            indicator_type = 1 if linktriggers else 0
        return cls(
            selementid1=str(raw.get("selementid1", "")).strip(),
            selementid2=str(raw.get("selementid2", "")).strip(),
            drawtype=_to_int(raw.get("drawtype")) or DEFAULT_LINK_DRAWTYPE,
            color=str(raw.get("color", DEFAULT_LINK_COLOR)),
            indicator_type=indicator_type,
            linktriggers=linktriggers,
        )

    def to_api(self) -> dict:
        # Deliberately no "linkid": on map.update Zabbix replaces a map's whole
        # link set from the selementid pairs in the payload, and treats "linkid"
        # as read-only/output-only here -- including it is what was causing
        # "Wrong fields for map link.".
        payload: dict = {
            "selementid1": self.selementid1,
            "selementid2": self.selementid2,
            "drawtype": self.drawtype,
            "color": self.color,
        }
        if self.linktriggers:
            payload["indicator_type"] = self.indicator_type
            payload["linktriggers"] = [trigger.to_api() for trigger in self.linktriggers]
        return payload


@dataclass(frozen=True)
class ZabbixMap:
    name: str
    width: int
    height: int
    selements: tuple[MapElement, ...] = ()
    links: tuple[MapLink, ...] = ()
    # Set only for a map that already exists in Zabbix.
    sysmapid: str | None = None
    label_format: str | None = None
    label_type_image: str | None = None

    @classmethod
    def from_api(cls, raw: dict) -> ZabbixMap:
        sysmapid = str(raw.get("sysmapid", "")).strip() or None
        return cls(
            name=str(raw.get("name", "")),
            width=_to_int(raw.get("width")) or 0,
            height=_to_int(raw.get("height")) or 0,
            selements=tuple(
                MapElement.from_api(item) for item in raw.get("selements") or [] if isinstance(item, dict)
            ),
            links=tuple(MapLink.from_api(item) for item in raw.get("links") or [] if isinstance(item, dict)),
            sysmapid=sysmapid,
            label_format=_optional_str(raw.get("label_format")),
            label_type_image=_optional_str(raw.get("label_type_image")),
        )

    def to_api_payload(self) -> dict:
        """Body for map.create; ZabbixClient.update_map adds "sysmapid" itself."""
        payload: dict = {
            "name": self.name,
            "width": str(self.width),
            "height": str(self.height),
            "selements": [element.to_api() for element in self.selements],
            "links": [link.to_api() for link in self.links],
        }
        if self.label_format is not None:
            payload["label_format"] = self.label_format
        if self.label_type_image is not None:
            payload["label_type_image"] = self.label_type_image
        return payload


def _optional_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
