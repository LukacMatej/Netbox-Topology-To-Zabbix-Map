from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass

from .models import TopologyGraph, TopologyNode
from .zabbix import ZabbixClient

DEFAULT_HOST_ICON_ID = "155"
GRID_STEP_X = 40
GRID_STEP_Y = 40

# Zabbix sysmap selement "elementtype" values
ELEMENT_TYPE_HOST = 0
ELEMENT_TYPE_IMAGE = 4

SKIPPED_NODE_MODE_SKIP = "skip"
SKIPPED_NODE_MODE_IMAGE = "image"


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncResult:
    created: bool
    map_name: str
    total_nodes: int
    matched_hosts: int
    skipped_nodes: int
    image_nodes: int
    total_links: int
    unresolved_link_rules: int
    unresolved_link_rule_details: tuple[str, ...]


def _normalize_host_pair(host_a: str, host_b: str) -> tuple[str, str]:
    return tuple(sorted((host_a.strip(), host_b.strip())))


def _hostid_from_selement(selement: dict) -> str | None:
    elements = selement.get("elements")
    if not isinstance(elements, list) or not elements:
        return None
    first = elements[0]
    if not isinstance(first, dict):
        return None
    hostid = str(first.get("hostid", "")).strip()
    return hostid or None


def _build_adjacency(graph: TopologyGraph) -> dict[str, set[str]]:
    node_ids = {node.node_id for node in graph.nodes}
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in graph.edges:
        if edge.source_id not in node_ids or edge.target_id not in node_ids:
            continue
        adjacency[edge.source_id].add(edge.target_id)
        adjacency[edge.target_id].add(edge.source_id)
    return adjacency


def _connected_components(node_ids: list[str], adjacency: dict[str, set[str]]) -> list[list[str]]:
    seen: set[str] = set()
    components: list[list[str]] = []

    for node_id in node_ids:
        if node_id in seen:
            continue
        queue: deque[str] = deque([node_id])
        seen.add(node_id)
        component: list[str] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in adjacency.get(current, set()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        components.append(component)

    return components


def _fruchterman_reingold_layout(
    component: list[str],
    adjacency: dict[str, set[str]],
    iterations: int = 120,
) -> tuple[dict[str, tuple[int, int]], int, int]:
    """Force-directed layout for a single connected component.

    Classic Fruchterman-Reingold: nodes repel each other, edges act as
    springs pulling connected nodes together, and a cooling "temperature"
    shrinks the max step size each iteration so the system settles instead
    of oscillating. Initial positions are placed deterministically (sorted
    node order around a circle, no RNG) so the same graph always produces
    the same layout on re-sync.
    """
    n = len(component)

    if n == 1:
        size = 140
        return {component[0]: (size // 2, size // 2)}, size, size

    # Canvas the simulation runs in scales with node count so dense
    # components get more breathing room without the whole map exploding.
    # The multiplier accounts for icon size plus the label drawn below each
    # node -- too small a canvas and nodes/labels end up overlapping.
    side = max(300, int(110 * math.sqrt(n)))
    width = height = side
    area = float(width * height)
    k = math.sqrt(area / n)  # ideal spring/edge length

    ordered = sorted(component)
    pos: dict[str, list[float]] = {}
    cx, cy = width / 2, height / 2
    radius = min(width, height) * 0.35
    for idx, node_id in enumerate(ordered):
        angle = 2 * math.pi * idx / n
        pos[node_id] = [cx + radius * math.cos(angle), cy + radius * math.sin(angle)]

    temperature = side / 10.0
    cooling = temperature / max(1, iterations)
    margin = 50.0

    for _ in range(iterations):
        disp: dict[str, list[float]] = {node_id: [0.0, 0.0] for node_id in component}

        # Repulsion: every pair of nodes pushes apart (Coulomb-like force).
        for i in range(n):
            a = ordered[i]
            ax, ay = pos[a]
            for j in range(i + 1, n):
                b = ordered[j]
                bx, by = pos[b]
                dx, dy = ax - bx, ay - by
                dist = math.hypot(dx, dy) or 0.01
                force = (k * k) / dist
                fx, fy = (dx / dist) * force, (dy / dist) * force
                disp[a][0] += fx
                disp[a][1] += fy
                disp[b][0] -= fx
                disp[b][1] -= fy

        # Attraction: connected nodes pull together (Hooke-like spring).
        seen_edges: set[tuple[str, str]] = set()
        for node_id in component:
            for neighbor in adjacency.get(node_id, set()):
                if neighbor not in pos:
                    continue
                edge_key = tuple(sorted((node_id, neighbor)))
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                a, b = edge_key
                ax, ay = pos[a]
                bx, by = pos[b]
                dx, dy = ax - bx, ay - by
                dist = math.hypot(dx, dy) or 0.01
                force = (dist * dist) / k
                fx, fy = (dx / dist) * force, (dy / dist) * force
                disp[a][0] -= fx
                disp[a][1] -= fy
                disp[b][0] += fx
                disp[b][1] += fy

        # Apply displacement capped by the current temperature, then clamp
        # to the component's canvas so nodes never drift off it.
        for node_id in component:
            dx, dy = disp[node_id]
            dist = math.hypot(dx, dy) or 0.01
            capped = min(dist, temperature)
            x, y = pos[node_id]
            x += (dx / dist) * capped
            y += (dy / dist) * capped
            x = min(width - margin, max(margin, x))
            y = min(height - margin, max(margin, y))
            pos[node_id] = [x, y]

        temperature = max(1.0, temperature - cooling)

    positions = {node_id: (int(round(x)), int(round(y))) for node_id, (x, y) in pos.items()}
    return positions, width, height


def _snap_to_free_grid(
    x: int,
    y: int,
    width: int,
    height: int,
    occupied: set[tuple[int, int]],
    grid_step_x: int,
    grid_step_y: int,
) -> tuple[int, int]:
    def clamp(px: int, py: int) -> tuple[int, int]:
        return min(width - 40, max(40, px)), min(height - 40, max(40, py))

    base_x = int(round(x / grid_step_x) * grid_step_x)
    base_y = int(round(y / grid_step_y) * grid_step_y)
    base_x, base_y = clamp(base_x, base_y)

    if (base_x, base_y) not in occupied:
        occupied.add((base_x, base_y))
        return base_x, base_y

    radius = 1
    while radius < 20:
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                candidate_x = base_x + dx * grid_step_x
                candidate_y = base_y + dy * grid_step_y
                candidate_x, candidate_y = clamp(candidate_x, candidate_y)
                if (candidate_x, candidate_y) not in occupied:
                    occupied.add((candidate_x, candidate_y))
                    return candidate_x, candidate_y
        radius += 1

    occupied.add((base_x, base_y))
    return base_x, base_y


def _layout_positions(
    graph: TopologyGraph,
    width: int,
    height: int,
    grid_x: int = GRID_STEP_X,
    grid_y: int = GRID_STEP_Y,
) -> dict[str, tuple[int, int]]:
    if not graph.nodes:
        return {}

    if len(graph.nodes) == 1:
        node = graph.nodes[0]
        return {node.node_id: (width // 2, height // 2)}

    adjacency = _build_adjacency(graph)
    node_ids = [node.node_id for node in graph.nodes]
    components = _connected_components(node_ids, adjacency)
    components.sort(key=len, reverse=True)

    padding = 45
    row_gap = 45
    column_gap = 45
    current_x = padding
    current_y = padding
    row_height = 0
    occupied_grid: set[tuple[int, int]] = set()

    positions: dict[str, tuple[int, int]] = {}
    for component in components:
        local_positions, comp_width, comp_height = _fruchterman_reingold_layout(component, adjacency)

        if current_x + comp_width > width - padding and current_x > padding:
            current_x = padding
            current_y += row_height + row_gap
            row_height = 0

        for node_id, (local_x, local_y) in local_positions.items():
            x = min(width - 40, max(40, current_x + local_x))
            y = min(height - 40, max(40, current_y + local_y))
            if len(component) == 1:
                x, y = _snap_to_free_grid(x, y, width, height, occupied_grid, grid_x, grid_y)
            positions[node_id] = (x, y)

        current_x += comp_width + column_gap
        row_height = max(row_height, comp_height)

    return positions


def build_map_payload(
    graph: TopologyGraph,
    hosts_by_name,
    zabbix: ZabbixClient,
    map_name: str,
    width: int,
    height: int,
    grid_x: int,
    grid_y: int,
    existing_map: dict | None,
    skipped_node_mode: str = SKIPPED_NODE_MODE_SKIP,
    skipped_node_icon_id: str = "",
) -> tuple[dict, int, int, int, int, tuple[str, ...]]:
    positions = _layout_positions(graph, width, height, grid_x, grid_y)

    existing_host_to_selementid: dict[str, str] = {}
    existing_label_to_image_selementid: dict[str, str] = {}
    existing_links_by_pair: dict[tuple[str, str], dict] = {}
    max_selement_id = 0
    if existing_map:
        for selement in existing_map.get("selements", []) or []:
            if not isinstance(selement, dict):
                continue
            selementid = str(selement.get("selementid", "")).strip()
            if selementid.isdigit():
                max_selement_id = max(max_selement_id, int(selementid))
            hostid = _hostid_from_selement(selement)
            if hostid and selementid:
                existing_host_to_selementid[hostid] = selementid
            elif selementid and str(selement.get("elementtype")) == str(ELEMENT_TYPE_IMAGE):
                label = str(selement.get("label", "")).strip()
                if label:
                    existing_label_to_image_selementid[label] = selementid

        for link in existing_map.get("links", []) or []:
            if not isinstance(link, dict):
                continue
            left = str(link.get("selementid1", "")).strip()
            right = str(link.get("selementid2", "")).strip()
            if left and right:
                existing_links_by_pair[tuple(sorted((left, right)))] = link

    selements: list[dict] = []
    selement_by_node_id: dict[str, str] = {}
    host_by_node_id: dict[str, object] = {}

    next_selement_id = max_selement_id + 1
    matched_host_count = 0
    image_node_count = 0
    image_icon_id = skipped_node_icon_id or DEFAULT_HOST_ICON_ID

    for node in graph.nodes:
        host = hosts_by_name.get(node.label)
        x, y = positions.get(node.node_id, (40 + next_selement_id * 20, 40 + next_selement_id * 20))

        if not host:
            if skipped_node_mode != SKIPPED_NODE_MODE_IMAGE:
                logger.debug(
                    "Skipping topology node without Zabbix host match node_id=%s label=%s",
                    node.node_id,
                    node.label,
                )
                continue

            selementid = existing_label_to_image_selementid.get(node.label)
            if not selementid:
                selementid = str(next_selement_id)
                next_selement_id += 1

            selements.append(
                {
                    "selementid": selementid,
                    "elementtype": ELEMENT_TYPE_IMAGE,
                    "elements": [],
                    "label": node.label,
                    "iconid_off": image_icon_id,
                    "x": x,
                    "y": y,
                }
            )
            selement_by_node_id[node.node_id] = selementid
            image_node_count += 1
            logger.debug(
                "Prepared image selement for unmatched node node_id=%s label=%s selementid=%s position=(%s,%s)",
                node.node_id,
                node.label,
                selementid,
                x,
                y,
            )
            continue

        selementid = existing_host_to_selementid.get(host.hostid)
        if not selementid:
            selementid = str(next_selement_id)
            next_selement_id += 1

        selements.append(
            {
                "selementid": selementid,
                "elementtype": ELEMENT_TYPE_HOST,
                "elements": [{"hostid": host.hostid}],
                "label": host.name or host.host,
                "iconid_off": DEFAULT_HOST_ICON_ID,
                "x": x,
                "y": y,
            }
        )
        selement_by_node_id[node.node_id] = selementid
        host_by_node_id[node.node_id] = host
        matched_host_count += 1
        logger.debug(
            "Prepared map selement node_id=%s label=%s hostid=%s selementid=%s position=(%s,%s)",
            node.node_id,
            node.label,
            host.hostid,
            selementid,
            x,
            y,
        )

    links: list[dict] = []
    edges_by_pair: dict[tuple[str, str], dict] = {}
    trigger_cache: dict[tuple[str, str], str | None] = {}
    unresolved_rules: set[tuple[str, str, str]] = set()
    for edge in graph.edges:
        logger.debug(
            "Processing topology edge source_id=%s target_id=%s trigger_names=%s",
            edge.source_id,
            edge.target_id,
            edge.trigger_names,
        )
        source_selementid = selement_by_node_id.get(edge.source_id)
        target_selementid = selement_by_node_id.get(edge.target_id)
        if not source_selementid or not target_selementid:
            logger.debug(
                "Skipping edge because source/target selement is missing source_id=%s target_id=%s",
                edge.source_id,
                edge.target_id,
            )
            continue

        source_host = host_by_node_id.get(edge.source_id)
        target_host = host_by_node_id.get(edge.target_id)
        pair = tuple(sorted((source_selementid, target_selementid)))

        if source_host and target_host:
            host_pair_key = _normalize_host_pair(source_host.name or source_host.host, target_host.name or target_host.host)
            hostids = [source_host.hostid, target_host.hostid]
        else:
            # One (or both) endpoints is an image element (unmatched node) with no
            # Zabbix host behind it, so there is nothing to resolve link triggers
            # against. The link itself is still drawn between the two selements.
            logger.debug(
                "Edge touches an unmatched/image node; drawing plain link without trigger "
                "resolution source_id=%s target_id=%s",
                edge.source_id,
                edge.target_id,
            )
            host_pair_key = None
            hostids = []

        pair_entry = edges_by_pair.setdefault(
            pair,
            {
                "hostids": hostids,
                "host_pair_key": host_pair_key,
                "trigger_names": [],
            },
        )

        if host_pair_key is not None and pair_entry["host_pair_key"] != host_pair_key:
            logger.debug(
                "Pair host key mismatch for pair=%s previous=%s current=%s",
                pair,
                pair_entry["host_pair_key"],
                host_pair_key,
            )

        existing_trigger_names = pair_entry["trigger_names"]
        for trigger_name in edge.trigger_names:
            normalized_name = str(trigger_name).strip()
            if normalized_name and normalized_name not in existing_trigger_names:
                existing_trigger_names.append(normalized_name)

        logger.debug(
            "Aggregated edge data pair=%s total_trigger_names=%s",
            pair,
            len(existing_trigger_names),
        )

    for pair, edge_data in edges_by_pair.items():
        link_payload = {
            "selementid1": pair[0],
            "selementid2": pair[1],
            "drawtype": 0,
            "color": "00AA00",
        }

        host_pair_key = edge_data["host_pair_key"]
        hostids = edge_data["hostids"]
        link_trigger_entries: list[dict] = []
        if host_pair_key is None:
            if edge_data["trigger_names"]:
                logger.debug(
                    "Skipping trigger resolution for link pair=%s: endpoint has no matched Zabbix host",
                    pair,
                )
        else:
            for trigger_name in edge_data["trigger_names"]:
                trigger_key = (host_pair_key[0], host_pair_key[1], trigger_name)
                trigger_id = trigger_cache.get(trigger_key)
                if trigger_key not in trigger_cache:
                    logger.debug(
                        "Resolving link trigger host_pair=%s trigger_name=%s",
                        host_pair_key,
                        trigger_name,
                    )
                    trigger_id = zabbix.find_trigger_id(
                        hostids=hostids,
                        trigger_name=trigger_name,
                        match="auto",
                    )
                    trigger_cache[trigger_key] = trigger_id

                if trigger_id:
                    logger.debug(
                        "Matched link trigger host_pair=%s trigger_name=%s triggerid=%s",
                        host_pair_key,
                        trigger_name,
                        trigger_id,
                    )
                    link_trigger_entries.append(
                        {
                            "triggerid": trigger_id,
                            "drawtype": "0",
                            "color": "FF0000",
                        }
                    )
                else:
                    logger.warning(
                        "Could not match cable trigger from NetBox host_pair=%s trigger_name=%s",
                        host_pair_key,
                        trigger_name,
                    )
                    unresolved_rules.add(
                        (host_pair_key[0], host_pair_key[1], trigger_name)
                    )

        existing_link = existing_links_by_pair.get(pair)
        if link_trigger_entries:
            link_payload["indicator_type"] = 1
            link_payload["linktriggers"] = link_trigger_entries
            logger.debug("Added %s link trigger entries to map link pair=%s", len(link_trigger_entries), pair)
        elif existing_link and existing_link.get("linktriggers"):
            link_payload["indicator_type"] = int(existing_link.get("indicator_type", 1))
            link_payload["linktriggers"] = existing_link.get("linktriggers", [])
            logger.debug("Preserved existing link triggers for pair=%s", pair)

        if existing_link and existing_link.get("linkid"):
            link_payload["linkid"] = str(existing_link["linkid"])

        links.append(link_payload)

    payload = {
        "name": map_name,
        "width": str(width),
        "height": str(height),
        "selements": selements,
        "links": links,
    }
    if image_node_count > 0:
        # By default Zabbix's map-wide label_type is "element name" (2), which
        # host elements resolve to their hostname just fine, but image-type
        # elements have no underlying host/name to resolve, so Zabbix falls
        # back to showing the literal element type ("Image") instead of our
        # static `label` text. Overriding just the image-element label mode
        # to "label" (0) makes the NetBox device name actually render, without
        # touching how host elements display their live name/status.
        #
        # Per-element-type label overrides (label_type_image and friends) are
        # only honored by Zabbix when label_format=1 ("custom label format");
        # with the default label_format=0 they're silently ignored and every
        # element keeps following the map-wide label_type, which is exactly
        # why images were still rendering the literal "Image" fallback.
        payload["label_format"] = "1"
        payload["label_type_image"] = "0"
    logger.info(
        "Built map payload name=%s matched_hosts=%s image_nodes=%s links=%s unresolved_link_rules=%s",
        map_name,
        matched_host_count,
        image_node_count,
        len(links),
        len(unresolved_rules),
    )
    unresolved_details = tuple(
        f"Cable trigger: {host_a} <-> {host_b} | trigger='{trigger_name}'"
        for host_a, host_b, trigger_name in sorted(unresolved_rules)
    )
    return payload, matched_host_count, image_node_count, len(links), len(unresolved_rules), unresolved_details


def sync_topology_to_zabbix_map(
    graph: TopologyGraph,
    zabbix: ZabbixClient,
    map_name: str,
    width: int,
    height: int,
    grid_x: int = GRID_STEP_X,
    grid_y: int = GRID_STEP_Y,
    skipped_node_mode: str = SKIPPED_NODE_MODE_SKIP,
    skipped_node_icon_id: str = "",
) -> SyncResult:
    topology_names = sorted({node.label for node in graph.nodes if node.label})
    logger.debug("Syncing topology labels=%s", topology_names)
    hosts = zabbix.get_hosts_by_names(topology_names)

    existing_map = zabbix.get_map_by_name(map_name)

    payload, matched_hosts, image_nodes, link_count, unresolved_link_rules, unresolved_details = build_map_payload(
        graph=graph,
        hosts_by_name=hosts,
        zabbix=zabbix,
        map_name=map_name,
        width=width,
        height=height,
        grid_x=max(10, grid_x),
        grid_y=max(10, grid_y),
        existing_map=existing_map,
        skipped_node_mode=skipped_node_mode,
        skipped_node_icon_id=skipped_node_icon_id,
    )

    created = existing_map is None

    if created:
        zabbix.create_map(payload)
    else:
        zabbix.update_map(existing_map["sysmapid"], payload)

    return SyncResult(
        created=created,
        map_name=map_name,
        total_nodes=len(graph.nodes),
        matched_hosts=matched_hosts,
        skipped_nodes=max(0, len(graph.nodes) - matched_hosts - image_nodes),
        image_nodes=image_nodes,
        total_links=link_count,
        unresolved_link_rules=unresolved_link_rules,
        unresolved_link_rule_details=unresolved_details,
    )
