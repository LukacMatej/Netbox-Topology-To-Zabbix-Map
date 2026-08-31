from __future__ import annotations

import html
import json
import logging
import re
from ast import literal_eval
from urllib.parse import parse_qsl, urlencode, urljoin
from xml.etree import ElementTree as ET

import requests

from .models import TopologyEdge, TopologyGraph, TopologyNode


logger = logging.getLogger(__name__)
ENRICHMENT_MARKER = "cable-detail-v2"


def _is_patch_panel_label(label: str) -> bool:
    normalized = str(label or "").strip().casefold()
    if not normalized:
        return False

    compact = re.sub(r"[\s_-]+", "", normalized)
    if "patchpanel" in compact:
        return True

    # Many installations name patch panels using PP abbreviations, e.g. DR1 PP_01 or PP D1.
    tokenized = re.sub(r"[_-]+", " ", normalized)
    tokens = [token for token in re.split(r"\s+", tokenized) if token]
    return any(token == "pp" for token in tokens)


def _merge_trigger_name_tuples(*trigger_sets: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    for trigger_names in trigger_sets:
        for name in trigger_names:
            if name not in merged:
                merged.append(name)
    return tuple(merged)


def _next_page_url(base_url: str, next_value) -> str | None:
    if not next_value:
        return None
    next_text = str(next_value).strip()
    if not next_text:
        return None
    return urljoin(f"{base_url}/", next_text)


def _normalize_trigger_names(value) -> tuple[str, ...]:
    items: list[str] = []

    def add_item(name) -> None:
        text = str(name or "").strip()
        if text and text not in items:
            items.append(text)

    def collect(obj) -> None:
        if isinstance(obj, str):
            text = obj.strip()
            if not text:
                return
            if text.startswith("[") or text.startswith("{"):
                try:
                    collect(json.loads(text))
                    return
                except json.JSONDecodeError:
                    try:
                        collect(literal_eval(text))
                        return
                    except (ValueError, SyntaxError):
                        pass
            add_item(text)
            return

        if isinstance(obj, dict):
            triggers = obj.get("triggers")
            if triggers is not None:
                collect(triggers)
            return

        if isinstance(obj, list):
            for element in obj:
                collect(element)

    collect(value)
    if items:
        logger.debug("Normalized trigger names raw=%r normalized=%s", value, items)
    return tuple(items)


def _extract_edge_trigger_names(edge: dict) -> tuple[str, ...]:
    candidates = [
        edge.get("zabbix_triggers"),
        (edge.get("custom_fields") or {}).get("zabbix_triggers") if isinstance(edge.get("custom_fields"), dict) else None,
    ]

    nested_data = edge.get("data")
    if isinstance(nested_data, dict):
        candidates.append(nested_data.get("zabbix_triggers"))
        custom_fields = nested_data.get("custom_fields")
        if isinstance(custom_fields, dict):
            candidates.append(custom_fields.get("zabbix_triggers"))

    nested_cable = edge.get("cable")
    if isinstance(nested_cable, dict):
        candidates.append(nested_cable.get("zabbix_triggers"))
        custom_fields = nested_cable.get("custom_fields")
        if isinstance(custom_fields, dict):
            candidates.append(custom_fields.get("zabbix_triggers"))

    for candidate in candidates:
        trigger_names = _normalize_trigger_names(candidate)
        if trigger_names:
            logger.debug(
                "Extracted trigger names from topology edge source=%s target=%s trigger_names=%s",
                edge.get("source") or edge.get("from") or edge.get("node_a") or edge.get("src"),
                edge.get("target") or edge.get("to") or edge.get("node_b") or edge.get("dst"),
                trigger_names,
            )
            return trigger_names

    return ()


def _extract_device_id_from_termination(value) -> str:
    if isinstance(value, list):
        for item in value:
            device_id = _extract_device_id_from_termination(item)
            if device_id:
                return device_id
        return ""

    if not isinstance(value, dict):
        return ""

    direct_id = str(value.get("device_id", "")).strip()
    if direct_id:
        return direct_id

    nested_device = value.get("device")
    if isinstance(nested_device, dict):
        nested_id = str(nested_device.get("id", "")).strip()
        if nested_id:
            return nested_id

    for key in ("object", "termination", "assigned_object", "connected_endpoint"):
        nested = value.get(key)
        device_id = _extract_device_id_from_termination(nested)
        if device_id:
            return device_id

    return ""


def _extract_cable_device_pair(cable: dict) -> tuple[str, str] | None:
    side_a = _extract_device_id_from_termination(
        cable.get("a_terminations")
        or cable.get("termination_a")
        or cable.get("a")
    )
    side_b = _extract_device_id_from_termination(
        cable.get("b_terminations")
        or cable.get("termination_b")
        or cable.get("b")
    )
    if side_a and side_b:
        return tuple(sorted((side_a, side_b)))
    return None


def _extract_cable_trigger_names(cable: dict) -> tuple[str, ...]:
    custom_fields = cable.get("custom_fields") if isinstance(cable.get("custom_fields"), dict) else {}
    candidates = [
        cable.get("zabbix_triggers"),
        custom_fields.get("zabbix_triggers"),
        custom_fields.get("zabbix-trigger"),
        custom_fields.get("zabbixTrigger"),
        custom_fields.get("triggers"),
    ]

    for key, value in custom_fields.items():
        lowered = str(key).lower()
        if "trigger" in lowered and "zabbix" in lowered:
            candidates.append(value)

    for candidate in candidates:
        trigger_names = _normalize_trigger_names(candidate)
        if trigger_names:
            return trigger_names
    return ()


def _extract_termination_url(value) -> str:
    if isinstance(value, list):
        for item in value:
            url = _extract_termination_url(item)
            if url:
                return url
        return ""

    if not isinstance(value, dict):
        return ""

    direct = str(value.get("url", "")).strip()
    if direct:
        return direct

    for key in ("object", "termination", "assigned_object", "connected_endpoint"):
        nested = value.get(key)
        url = _extract_termination_url(nested)
        if url:
            return url

    return ""


class NetBoxClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        required_tag: str = "",
        ignored_device_roles: tuple[str, ...] = (),
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.required_tag = required_tag.strip()
        self.ignored_device_roles = tuple(role.strip() for role in ignored_device_roles if role.strip())
        self.ignored_role_variants: set[str] = set()
        for role in self.ignored_device_roles:
            self.ignored_role_variants |= _tag_variants(role)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Token {token}",
                "Accept": "application/json",
            }
        )
        logger.info(
            "NetBox client initialized required_tag=%s ignored_device_roles=%s",
            self.required_tag or "<none>",
            ",".join(self.ignored_device_roles) or "<none>",
        )

    def _fetch_paginated_results(self, path: str, params: dict | None = None) -> list[dict]:
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        all_results: list[dict] = []
        current_params = dict(params or {})

        while True:
            logger.debug("Fetching paginated NetBox endpoint url=%s params=%s", url, current_params or None)
            response = self.session.get(url, params=current_params or None, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()

            if isinstance(payload, dict) and isinstance(payload.get("results"), list):
                page_results = [item for item in payload.get("results", []) if isinstance(item, dict)]
                all_results.extend(page_results)
                next_url = _next_page_url(self.base_url, payload.get("next"))
                if not next_url:
                    break
                url = next_url
                current_params = {}
                continue

            if isinstance(payload, list):
                all_results.extend(item for item in payload if isinstance(item, dict))
            break

        logger.debug(
            "Fetched paginated NetBox endpoint path=%s total_results=%s",
            path,
            len(all_results),
        )
        return all_results

    def _fetch_devices_by_ids(self, device_ids: set[str]) -> dict[str, dict]:
        if not device_ids:
            return {}

        params: list[tuple[str, str | int]] = [("limit", 0)]
        for device_id in sorted(device_ids):
            params.append(("id", device_id))

        devices_url = urljoin(f"{self.base_url}/", "api/dcim/devices/")
        response = self.session.get(devices_url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()

        by_id: dict[str, dict] = {}
        for item in payload.get("results", []):
            if not isinstance(item, dict):
                continue
            device_id = str(item.get("id", "")).strip()
            if device_id:
                by_id[device_id] = item
        return by_id

    def _fetch_cables_by_ids(self, cable_ids: set[str]) -> dict[str, dict]:
        if not cable_ids:
            return {}

        params: list[tuple[str, str | int]] = [("limit", 0)]
        for cable_id in sorted(cable_ids):
            params.append(("id", cable_id))

        cables_url = urljoin(f"{self.base_url}/", "api/dcim/cables/")
        logger.debug("Batch-fetching cable details count=%s", len(cable_ids))
        response = self.session.get(cables_url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()

        by_id: dict[str, dict] = {}
        for item in payload.get("results", []):
            if not isinstance(item, dict):
                continue
            cable_id = str(item.get("id", "")).strip()
            if cable_id:
                by_id[cable_id] = item
        return by_id

    def fetch_devices_by_ids(self, device_ids: set[str]) -> dict[str, dict]:
        return self._fetch_devices_by_ids(device_ids)

    def get_cable(self, cable_id: str | int) -> dict:
        url = urljoin(f"{self.base_url}/", f"api/dcim/cables/{cable_id}/")
        logger.debug("Fetching NetBox cable url=%s", url)
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected NetBox cable response for cable_id={cable_id}")
        return payload

    def get_cable_trigger_names(self, cable: dict) -> tuple[str, ...]:
        return _extract_cable_trigger_names(cable)

    def resolve_device_id_from_termination_url(self, url: str) -> str:
        normalized_url = str(url or "").strip()
        if not normalized_url:
            return ""
        try:
            logger.debug("Resolving termination endpoint url=%s", normalized_url)
            response = self.session.get(normalized_url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.debug("Could not resolve endpoint url=%s error=%s", normalized_url, exc)
            return ""

        device_id = _extract_device_id_from_termination(payload)
        if not device_id and isinstance(payload, dict):
            nested_device = payload.get("device")
            if isinstance(nested_device, dict):
                device_id = str(nested_device.get("id", "")).strip()
        return device_id

    def resolve_cable_device_pair(self, cable: dict) -> tuple[str, str] | None:
        direct_pair = _extract_cable_device_pair(cable)
        if direct_pair:
            return direct_pair

        side_a_term = cable.get("a_terminations") or cable.get("termination_a") or cable.get("a")
        side_b_term = cable.get("b_terminations") or cable.get("termination_b") or cable.get("b")
        side_a_url = _extract_termination_url(side_a_term)
        side_b_url = _extract_termination_url(side_b_term)
        if not side_a_url or not side_b_url:
            return None

        side_a_device = self.resolve_device_id_from_termination_url(side_a_url)
        side_b_device = self.resolve_device_id_from_termination_url(side_b_url)
        if side_a_device and side_b_device:
            return tuple(sorted((side_a_device, side_b_device)))
        return None

    def set_cable_custom_field(self, cable_id: str | int, field_name: str, value) -> dict:
        url = urljoin(f"{self.base_url}/", f"api/dcim/cables/{cable_id}/")
        payload = {"custom_fields": {field_name: value}}
        logger.info("Updating NetBox cable custom field cable_id=%s field=%s", cable_id, field_name)
        response = self.session.patch(url, json=payload, timeout=self.timeout)
        if not response.ok:
            logger.error(
                "NetBox rejected cable custom field update cable_id=%s field=%s status=%s body=%s",
                cable_id,
                field_name,
                response.status_code,
                response.text,
            )
            raise ValueError(
                f"NetBox rejected update to cable_id={cable_id} field={field_name}: "
                f"{response.status_code} {response.text}"
            )
        return response.json()

    def fetch_topology(self, path: str, query: str = "") -> TopologyGraph:
        path = path if path.startswith("/") else f"/{path}"
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        if path.rstrip("/").endswith("xml-export"):
            query = _merge_query_defaults(
                query=query,
                defaults={
                    "show_unconnected": "True",
                    "show_cables": "True",
                    "limit": "0",
                },
            )
        if query:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{query}"

        logger.info("Fetching NetBox topology url=%s", url)

        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        logger.debug("NetBox topology response content_type=%s", content_type)
        if "xml" in content_type:
            graph = _parse_topology_xml(response.text)
            graph = self._enrich_xml_node_labels(graph)
            graph = self._enrich_xml_edge_triggers(graph)
            graph = self._collapse_patch_panel_passthrough(graph)
            graph = self._filter_xml_nodes_by_tag(graph)
            logger.info(
                "Parsed XML topology nodes=%s edges=%s edges_with_triggers=%s",
                len(graph.nodes),
                len(graph.edges),
                sum(1 for edge in graph.edges if edge.trigger_names),
            )
            return graph
        payload = response.json()
        graph = _parse_topology_json(payload)
        logger.info(
            "Parsed JSON topology nodes=%s edges=%s edges_with_triggers=%s",
            len(graph.nodes),
            len(graph.edges),
            sum(1 for edge in graph.edges if edge.trigger_names),
        )
        return graph

    def _filter_xml_nodes_by_tag(self, graph: TopologyGraph) -> TopologyGraph:
        if not self.required_tag:
            return graph

        node_to_device: dict[str, str] = {}
        for node in graph.nodes:
            match = re.fullmatch(r"node_(\d+)", node.node_id)
            if match:
                node_to_device[node.node_id] = match.group(1)

        if not node_to_device:
            return graph

        logger.debug("Filtering XML topology nodes by tag=%s via api/dcim/devices/", self.required_tag)
        devices_by_id = self._fetch_devices_by_ids(set(node_to_device.values()))

        required_variants = _tag_variants(self.required_tag)
        allowed_device_ids: set[str] = set()

        for item in devices_by_id.values():
            if not isinstance(item, dict):
                continue
            device_id = str(item.get("id", "")).strip()
            if not device_id:
                continue

            tags = item.get("tags", [])
            tag_texts: list[str] = []
            if isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, dict):
                        tag_texts.extend([str(tag.get("name", "")), str(tag.get("slug", ""))])
                    else:
                        tag_texts.append(str(tag))

            device_variants: set[str] = set()
            for text in tag_texts:
                device_variants |= _tag_variants(text)

            if required_variants & device_variants:
                allowed_device_ids.add(device_id)

        allowed_nodes = {
            node_id
            for node_id, device_id in node_to_device.items()
            if device_id in allowed_device_ids
        }

        filtered_nodes = [
            node
            for node in graph.nodes
            if node.node_id not in node_to_device or node.node_id in allowed_nodes
        ]
        kept_ids = {node.node_id for node in filtered_nodes}
        filtered_edges = [
            edge
            for edge in graph.edges
            if edge.source_id in kept_ids and edge.target_id in kept_ids
        ]

        logger.info(
            "Filtered XML topology by tag kept_nodes=%s removed_nodes=%s kept_edges=%s",
            len(filtered_nodes),
            len(graph.nodes) - len(filtered_nodes),
            len(filtered_edges),
        )

        return TopologyGraph(nodes=filtered_nodes, edges=filtered_edges)

    def _enrich_xml_node_labels(self, graph: TopologyGraph) -> TopologyGraph:
        unresolved: dict[str, str] = {}
        for node in graph.nodes:
            if node.label and node.label != node.node_id:
                continue
            match = re.fullmatch(r"node_(\d+)", node.node_id)
            if match:
                unresolved[node.node_id] = match.group(1)

        if not unresolved:
            return graph

        logger.debug("Enriching XML node labels via api/dcim/devices/")
        devices_by_id = self._fetch_devices_by_ids(set(unresolved.values()))

        device_names: dict[str, str] = {}
        for item in devices_by_id.values():
            if not isinstance(item, dict):
                continue
            device_id = str(item.get("id", "")).strip()
            device_name = _first_present(item, ["name", "display"])
            if device_id and device_name:
                device_names[device_id] = device_name

        enriched_nodes: list[TopologyNode] = []
        for node in graph.nodes:
            resolved_id = unresolved.get(node.node_id)
            if resolved_id and resolved_id in device_names:
                logger.debug(
                    "Resolved XML node label node_id=%s device_id=%s label=%s",
                    node.node_id,
                    resolved_id,
                    device_names[resolved_id],
                )
                enriched_nodes.append(TopologyNode(node_id=node.node_id, label=device_names[resolved_id]))
            else:
                enriched_nodes.append(node)

        return TopologyGraph(nodes=enriched_nodes, edges=graph.edges)

    def _enrich_xml_edge_triggers(self, graph: TopologyGraph) -> TopologyGraph:
        if not graph.edges:
            return graph

        logger.info("XML cable trigger enrichment mode=%s", ENRICHMENT_MARKER)

        node_to_device: dict[str, str] = {}
        for node in graph.nodes:
            match = re.fullmatch(r"node_(\d+)", node.node_id)
            if match:
                node_to_device[node.node_id] = match.group(1)

        if not node_to_device:
            logger.debug("Skipping XML edge trigger enrichment because no node_<id> identifiers were found")
            return graph

        cables_path = "api/dcim/cables/"
        cable_results = self._fetch_paginated_results(
            cables_path,
            params={"limit": 200, "brief": 0},
        )
        logger.debug("Enriching XML edge triggers via %s results=%s", cables_path, len(cable_results))

        relevant_device_ids = set(node_to_device.values())
        triggers_by_device_pair: dict[tuple[str, str], list[str]] = {}
        endpoint_device_cache: dict[str, str] = {}

        cable_ids_needing_detail: set[str] = set()
        for cable in cable_results:
            needs_detail_fetch = not any(
                key in cable
                for key in ("custom_fields", "a_terminations", "b_terminations", "termination_a", "termination_b")
            )
            if needs_detail_fetch:
                cable_id = str(cable.get("id", "")).strip()
                if cable_id:
                    cable_ids_needing_detail.add(cable_id)

        if cable_ids_needing_detail:
            logger.debug(
                "%s of %s cables are missing custom_fields/terminations in the list response; "
                "fetching them in one batched request instead of one-by-one",
                len(cable_ids_needing_detail),
                len(cable_results),
            )
        cable_details_by_id = self._fetch_cables_by_ids(cable_ids_needing_detail)

        def resolve_device_id_from_endpoint_url(endpoint_url: str) -> str:
            normalized_url = endpoint_url.strip()
            if not normalized_url:
                return ""
            if normalized_url in endpoint_device_cache:
                return endpoint_device_cache[normalized_url]

            endpoint_device_cache[normalized_url] = ""
            try:
                logger.debug("Resolving termination endpoint url=%s", normalized_url)
                endpoint_response = self.session.get(normalized_url, timeout=self.timeout)
                endpoint_response.raise_for_status()
                endpoint_payload = endpoint_response.json()
            except Exception as exc:
                logger.debug("Could not resolve endpoint url=%s error=%s", normalized_url, exc)
                return ""

            device_id = _extract_device_id_from_termination(endpoint_payload)
            if not device_id and isinstance(endpoint_payload, dict):
                nested_device = endpoint_payload.get("device")
                if isinstance(nested_device, dict):
                    device_id = str(nested_device.get("id", "")).strip()
                elif isinstance(nested_device, int):
                    device_id = str(nested_device).strip()

            endpoint_device_cache[normalized_url] = device_id
            if device_id:
                logger.debug("Resolved termination endpoint url=%s device_id=%s", normalized_url, device_id)
            return device_id

        def resolve_cable_device_pair(cable: dict) -> tuple[str, str] | None:
            direct_pair = _extract_cable_device_pair(cable)
            if direct_pair:
                return direct_pair

            side_a_term = (
                cable.get("a_terminations")
                or cable.get("termination_a")
                or cable.get("a")
            )
            side_b_term = (
                cable.get("b_terminations")
                or cable.get("termination_b")
                or cable.get("b")
            )
            side_a_url = _extract_termination_url(side_a_term)
            side_b_url = _extract_termination_url(side_b_term)
            if not side_a_url or not side_b_url:
                return None

            side_a_device = resolve_device_id_from_endpoint_url(side_a_url)
            side_b_device = resolve_device_id_from_endpoint_url(side_b_url)
            if side_a_device and side_b_device:
                return tuple(sorted((side_a_device, side_b_device)))
            return None

        def resolve_cable_details(cable: dict) -> dict:
            cable_id = str(cable.get("id", "")).strip()
            return cable_details_by_id.get(cable_id, cable)

        for cable in cable_results:
            cable = resolve_cable_details(cable)

            trigger_names = _extract_cable_trigger_names(cable)
            if not trigger_names:
                custom_fields = cable.get("custom_fields")
                logger.debug(
                    "Cable has no usable trigger custom field cable_id=%s custom_field_keys=%s",
                    cable.get("id"),
                    sorted(custom_fields.keys()) if isinstance(custom_fields, dict) else [],
                )
                continue

            device_pair = resolve_cable_device_pair(cable)
            if not device_pair:
                logger.debug(
                    "Skipping cable without resolvable device pair cable_id=%s keys=%s",
                    cable.get("id"),
                    sorted(cable.keys()),
                )
                continue

            if device_pair[0] not in relevant_device_ids or device_pair[1] not in relevant_device_ids:
                continue

            existing = triggers_by_device_pair.setdefault(device_pair, [])
            for trigger_name in trigger_names:
                if trigger_name not in existing:
                    existing.append(trigger_name)

            logger.debug(
                "Collected cable trigger metadata cable_id=%s device_pair=%s trigger_names=%s",
                cable.get("id"),
                device_pair,
                trigger_names,
            )

        if not triggers_by_device_pair:
            logger.warning(
                "No cable trigger metadata could be mapped to XML topology edges. "
                "Check whether NetBox cable API returns custom_fields and termination device information."
            )

        enriched_edges: list[TopologyEdge] = []
        enriched_count = 0
        for edge in graph.edges:
            source_device = node_to_device.get(edge.source_id)
            target_device = node_to_device.get(edge.target_id)
            trigger_names = edge.trigger_names
            if source_device and target_device:
                device_pair = tuple(sorted((source_device, target_device)))
                fallback_trigger_names = tuple(triggers_by_device_pair.get(device_pair, []))
                if fallback_trigger_names and not trigger_names:
                    trigger_names = fallback_trigger_names
                    enriched_count += 1
                    logger.debug(
                        "Enriched XML edge with cable triggers source_id=%s target_id=%s device_pair=%s trigger_names=%s",
                        edge.source_id,
                        edge.target_id,
                        device_pair,
                        trigger_names,
                    )

            enriched_edges.append(
                TopologyEdge(
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    trigger_names=trigger_names,
                )
            )

        logger.info(
            "XML edge trigger enrichment finished enriched_edges=%s total_edges=%s candidate_pairs=%s",
            enriched_count,
            len(graph.edges),
            len(triggers_by_device_pair),
        )
        return TopologyGraph(nodes=graph.nodes, edges=enriched_edges)

    def _collapse_patch_panel_passthrough(self, graph: TopologyGraph) -> TopologyGraph:
        if not graph.nodes or not graph.edges:
            return graph

        node_by_id = {node.node_id: node for node in graph.nodes}
        passthrough_ids: set[str] = set()
        if "patchpanel" in self.ignored_role_variants:
            passthrough_ids = {
                node.node_id
                for node in graph.nodes
                if _is_patch_panel_label(node.label)
            }

        if self.ignored_role_variants:
            node_to_device: dict[str, str] = {}
            for node in graph.nodes:
                match = re.fullmatch(r"node_(\d+)", node.node_id)
                if match:
                    node_to_device[node.node_id] = match.group(1)

            if node_to_device:
                devices_by_id = self._fetch_devices_by_ids(set(node_to_device.values()))
                for node_id, device_id in node_to_device.items():
                    device = devices_by_id.get(device_id)
                    if not device:
                        continue
                    role_variants = _extract_device_role_variants(device)
                    if self.ignored_role_variants & role_variants:
                        passthrough_ids.add(node_id)
                        logger.debug(
                            "Marked node as passthrough by role node_id=%s label=%s role_variants=%s",
                            node_id,
                            node_by_id.get(node_id).label if node_id in node_by_id else "",
                            sorted(role_variants),
                        )

        if not passthrough_ids:
            return graph

        current_edges = list(graph.edges)
        collapsed_count = 0

        while True:
            edge_indexes_by_node: dict[str, list[int]] = {node_id: [] for node_id in node_by_id}
            for index, edge in enumerate(current_edges):
                edge_indexes_by_node.setdefault(edge.source_id, []).append(index)
                edge_indexes_by_node.setdefault(edge.target_id, []).append(index)

            collapsed_any = False
            for panel_id in list(passthrough_ids):
                if panel_id not in node_by_id:
                    continue

                incident_indexes = edge_indexes_by_node.get(panel_id, [])
                if len(incident_indexes) != 2:
                    continue

                first_edge = current_edges[incident_indexes[0]]
                second_edge = current_edges[incident_indexes[1]]

                first_neighbor = first_edge.target_id if first_edge.source_id == panel_id else first_edge.source_id
                second_neighbor = second_edge.target_id if second_edge.source_id == panel_id else second_edge.source_id
                if not first_neighbor or not second_neighbor or first_neighbor == second_neighbor:
                    continue
                if first_neighbor not in node_by_id or second_neighbor not in node_by_id:
                    continue

                passthrough_edge = TopologyEdge(
                    source_id=first_neighbor,
                    target_id=second_neighbor,
                    trigger_names=_merge_trigger_name_tuples(first_edge.trigger_names, second_edge.trigger_names),
                )

                next_edges: list[TopologyEdge] = []
                for index, edge in enumerate(current_edges):
                    if index in incident_indexes:
                        continue
                    next_edges.append(edge)
                next_edges.append(passthrough_edge)
                current_edges = next_edges

                logger.info(
                    "Collapsed patch panel node node_id=%s label=%s neighbors=(%s,%s) trigger_names=%s",
                    panel_id,
                    node_by_id[panel_id].label,
                    first_neighbor,
                    second_neighbor,
                    passthrough_edge.trigger_names,
                )

                node_by_id.pop(panel_id, None)
                passthrough_ids.discard(panel_id)
                collapsed_count += 1
                collapsed_any = True
                break

            if not collapsed_any:
                break

        # Any ignored-role/patch-panel node that couldn't be spliced through
        # (degree 0, 1, or 3+) would otherwise leak through untouched and
        # show up on the map as an unmatched node. "Ignored" should mean
        # ignored regardless of how many cables the device has, so drop
        # these outright along with their incident edges.
        remaining_passthrough_ids = {pid for pid in passthrough_ids if pid in node_by_id}
        if remaining_passthrough_ids:
            for panel_id in remaining_passthrough_ids:
                degree = sum(
                    1
                    for edge in current_edges
                    if edge.source_id == panel_id or edge.target_id == panel_id
                )
                logger.info(
                    "Dropping ignored-role/patch-panel node with degree=%s (not exactly 2, "
                    "cannot splice through) node_id=%s label=%s",
                    degree,
                    panel_id,
                    node_by_id[panel_id].label,
                )
                node_by_id.pop(panel_id, None)

            current_edges = [
                edge
                for edge in current_edges
                if edge.source_id not in remaining_passthrough_ids
                and edge.target_id not in remaining_passthrough_ids
            ]
            collapsed_count += len(remaining_passthrough_ids)

        if collapsed_count == 0:
            return graph

        remaining_ids = set(node_by_id.keys())
        deduplicated_edges_by_pair: dict[tuple[str, str], tuple[str, ...]] = {}
        for edge in current_edges:
            if edge.source_id not in remaining_ids or edge.target_id not in remaining_ids:
                continue
            pair = tuple(sorted((edge.source_id, edge.target_id)))
            existing = deduplicated_edges_by_pair.get(pair, ())
            deduplicated_edges_by_pair[pair] = _merge_trigger_name_tuples(existing, edge.trigger_names)

        deduplicated_edges = [
            TopologyEdge(source_id=pair[0], target_id=pair[1], trigger_names=trigger_names)
            for pair, trigger_names in deduplicated_edges_by_pair.items()
        ]

        logger.info(
            "Passthrough collapse finished removed_nodes=%s remaining_nodes=%s remaining_edges=%s",
            collapsed_count,
            len(node_by_id),
            len(deduplicated_edges),
        )
        return TopologyGraph(nodes=list(node_by_id.values()), edges=deduplicated_edges)


def _extract_device_role_variants(device: dict) -> set[str]:
    role_values: list[str] = []

    for key in ("role", "device_role"):
        value = device.get(key)
        if isinstance(value, dict):
            for field in ("name", "slug", "display"):
                role_text = str(value.get(field, "")).strip()
                if role_text:
                    role_values.append(role_text)
        else:
            role_text = str(value or "").strip()
            if role_text:
                role_values.append(role_text)

    variants: set[str] = set()
    for role_text in role_values:
        variants |= _tag_variants(role_text)
    return variants


def _first_present(item: dict, keys: list[str]) -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _extract_node_label(node: dict) -> str:
    nested_device = node.get("device")
    if isinstance(nested_device, dict):
        label = _first_present(nested_device, ["name", "display", "label"])
        if label:
            return label

    nested_data = node.get("data")
    if isinstance(nested_data, dict):
        label = _first_present(nested_data, ["label", "name", "display"])
        if label:
            return label

    return _first_present(node, ["label", "name", "title", "text", "display"]) or _first_present(node, ["id"])


def _parse_topology_json(payload: dict) -> TopologyGraph:
    if not isinstance(payload, dict):
        raise ValueError("Unexpected NetBox topology response format")

    nodes_raw = payload.get("nodes")
    edges_raw = payload.get("edges")

    if not isinstance(nodes_raw, list) and isinstance(payload.get("data"), dict):
        nodes_raw = payload["data"].get("nodes")
    if not isinstance(edges_raw, list) and isinstance(payload.get("data"), dict):
        edges_raw = payload["data"].get("edges")

    if not isinstance(nodes_raw, list):
        nodes_raw = payload.get("vertices", [])
    if not isinstance(edges_raw, list):
        edges_raw = payload.get("links", [])

    nodes: list[TopologyNode] = []
    for raw in nodes_raw or []:
        if not isinstance(raw, dict):
            continue
        node_id = _first_present(raw, ["id", "node_id", "key"])
        if not node_id:
            continue
        nodes.append(TopologyNode(node_id=node_id, label=_extract_node_label(raw)))
        logger.debug("Parsed topology node node_id=%s label=%s", node_id, nodes[-1].label)

    edges: list[TopologyEdge] = []
    for raw in edges_raw or []:
        if not isinstance(raw, dict):
            continue
        source = _first_present(raw, ["source", "from", "node_a", "src"])
        target = _first_present(raw, ["target", "to", "node_b", "dst"])
        if source and target:
            trigger_names = _extract_edge_trigger_names(raw)
            edges.append(
                TopologyEdge(
                    source_id=source,
                    target_id=target,
                    trigger_names=trigger_names,
                )
            )
            logger.debug(
                "Parsed topology edge source=%s target=%s trigger_names=%s",
                source,
                target,
                trigger_names,
            )

    return TopologyGraph(nodes=nodes, edges=edges)


def _clean_xml_label(text: str | None) -> str:
    if not text:
        return ""
    return html.unescape(text).replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ").strip()


def _merge_query_defaults(query: str, defaults: dict[str, str]) -> str:
    merged = dict(parse_qsl(query, keep_blank_values=True))
    for key, value in defaults.items():
        merged.setdefault(key, value)
    return urlencode(merged)


def _tag_variants(value: str) -> set[str]:
    text = str(value or "").strip().casefold()
    if not text:
        return set()
    variants = {text}
    variants.add(text.replace(" ", "-"))
    variants.add(text.replace("-", " "))
    variants.add(text.replace("_", "-"))
    variants.add(text.replace("-", "_"))
    variants.add(text.replace(" ", ""))
    variants.add(text.replace("-", "").replace("_", "").replace(" ", ""))
    return variants


def _parse_topology_xml(payload: str) -> TopologyGraph:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML topology payload: {exc}") from exc

    nodes: list[TopologyNode] = []
    edges: list[TopologyEdge] = []

    vertex_ids: set[str] = set()
    for cell in root.findall(".//mxCell"):
        node_id = (cell.attrib.get("id") or "").strip()
        if not node_id or node_id in {"0", "1"}:
            continue

        if cell.attrib.get("vertex") == "1":
            value = _clean_xml_label(cell.attrib.get("value"))
            label = value or node_id
            nodes.append(TopologyNode(node_id=node_id, label=label))
            vertex_ids.add(node_id)
            logger.debug("Parsed XML topology node node_id=%s label=%s", node_id, label)

    for cell in root.findall(".//mxCell"):
        if cell.attrib.get("edge") != "1":
            continue
        source_id = (cell.attrib.get("source") or "").strip()
        target_id = (cell.attrib.get("target") or "").strip()
        if source_id and target_id and source_id in vertex_ids and target_id in vertex_ids:
            edges.append(TopologyEdge(source_id=source_id, target_id=target_id))
            logger.debug("Parsed XML topology edge source_id=%s target_id=%s", source_id, target_id)

    return TopologyGraph(nodes=nodes, edges=edges)
