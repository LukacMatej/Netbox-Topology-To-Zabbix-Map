from __future__ import annotations

import json
import logging
import math
from collections import deque
from dataclasses import replace

from .models import (
    ELEMENT_TYPE_HOST,
    ELEMENT_TYPE_IMAGE,
    DevicePositionRecord,
    MapElement,
    MapLink,
    MapLinkTrigger,
    SyncResult,
    TopologyGraph,
    TopologyNode,
    ZabbixMap,
)
from .netbox import DEFAULT_POSITION_FIELD, NetBoxClient, positions_for_map
from .zabbix import ZabbixClient

DEFAULT_HOST_ICON_ID = "155"
GRID_STEP_X = 40
GRID_STEP_Y = 40

SKIPPED_NODE_MODE_SKIP = "skip"
SKIPPED_NODE_MODE_IMAGE = "image"


logger = logging.getLogger(__name__)


def _normalize_host_pair(host_a: str, host_b: str) -> tuple[str, str]:
    return tuple(sorted((host_a.strip(), host_b.strip())))


def _resolve_fixed_positions(
    graph: TopologyGraph,
    hosts_by_name,
    existing_map: ZabbixMap | None,
    stored_positions: dict[str, tuple[int, int]],
) -> dict[str, tuple[int, int]]:
    """Resolve each node's already-known position, if any.

    Precedence: a live position on the existing Zabbix map (freshest --
    covers a human dragging the element around in the Zabbix UI) beats a
    position stored on the NetBox device (covers the map having been deleted
    and recreated), which beats letting the node fall through to auto-layout.
    """
    hostid_to_xy: dict[str, tuple[int, int]] = {}
    if existing_map:
        for selement in existing_map.selements:
            if selement.hostid and selement.x is not None and selement.y is not None:
                hostid_to_xy[selement.hostid] = (selement.x, selement.y)

    fixed: dict[str, tuple[int, int]] = {}
    for node in graph.nodes:
        host = hosts_by_name.get(node.label)
        if host and host.hostid in hostid_to_xy:
            fixed[node.node_id] = hostid_to_xy[host.hostid]
        elif node.label in stored_positions:
            fixed[node.node_id] = stored_positions[node.label]
    return fixed


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
    iterations: int = 150,
    target_area: float | None = None,
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
    # node -- too small a canvas and nodes/labels end up overlapping. Large,
    # heavily-meshed components additionally get a share of the overall map
    # area (target_area, from _layout_positions) so they aren't squeezed
    # into a small square while the rest of the canvas sits empty.
    min_side = max(300, int(150 * math.sqrt(n)))
    side = max(min_side, int(math.sqrt(target_area))) if target_area else min_side
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
    fixed_positions: dict[str, tuple[int, int]] | None = None,
) -> dict[str, tuple[int, int]]:
    # Nodes with a known position (a live Zabbix element or a value stored on
    # the NetBox device, resolved by the caller) are pinned and excluded from
    # the force-directed simulation entirely -- only genuinely new nodes get
    # auto-laid-out, so re-syncing never disturbs a manually placed node.
    fixed_positions = dict(fixed_positions or {})

    free_nodes = [node for node in graph.nodes if node.node_id not in fixed_positions]
    if not free_nodes:
        return fixed_positions

    free_edges = [
        edge
        for edge in graph.edges
        if edge.source_id not in fixed_positions and edge.target_id not in fixed_positions
    ]
    free_graph = TopologyGraph(nodes=free_nodes, edges=free_edges)

    if len(free_graph.nodes) == 1:
        node = free_graph.nodes[0]
        return {**fixed_positions, node.node_id: (width // 2, height // 2)}

    adjacency = _build_adjacency(free_graph)
    node_ids = [node.node_id for node in free_graph.nodes]
    components = _connected_components(node_ids, adjacency)
    components.sort(key=len, reverse=True)

    padding = 45
    row_gap = 45
    column_gap = 45
    current_x = padding
    current_y = padding
    row_height = 0
    # Seed with already-fixed positions so a newly auto-laid-out node never
    # snaps onto a cell a manually/previously placed node already occupies.
    occupied_grid: set[tuple[int, int]] = set(fixed_positions.values())

    # Give each component a share of the whole map proportional to its node
    # count, instead of only sizing it off its own node count. Without this,
    # one big, densely-meshed component and a handful of single-node
    # components each get a canvas sized the same way, so the big component
    # ends up squeezed into a small square while most of the map sits empty.
    total_nodes = sum(len(component) for component in components) or 1
    available_area = float(max(1, width * height))
    packing_efficiency = 0.6  # leaves room for padding/gaps between components
    max_side = float(max(300, min(width, height) - 2 * padding))

    positions: dict[str, tuple[int, int]] = dict(fixed_positions)
    for component in components:
        share = len(component) / total_nodes
        target_area = min(available_area * packing_efficiency * share, max_side * max_side)
        local_positions, comp_width, comp_height = _fruchterman_reingold_layout(
            component, adjacency, target_area=target_area
        )

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


def build_zabbix_map(
    graph: TopologyGraph,
    hosts_by_name,
    zabbix: ZabbixClient,
    map_name: str,
    width: int,
    height: int,
    grid_x: int,
    grid_y: int,
    existing_map: ZabbixMap | None,
    skipped_node_mode: str = SKIPPED_NODE_MODE_SKIP,
    skipped_node_icon_id: str = "",
    stored_positions: dict[str, tuple[int, int]] | None = None,
    iconmapid: str | None = None,
) -> tuple[ZabbixMap, int, int, int, int, tuple[str, ...], dict[str, tuple[int, int]]]:
    fixed_positions = _resolve_fixed_positions(graph, hosts_by_name, existing_map, stored_positions or {})
    positions = _layout_positions(graph, width, height, grid_x, grid_y, fixed_positions=fixed_positions)

    existing_host_to_selementid: dict[str, str] = {}
    existing_label_to_image_selementid: dict[str, str] = {}
    existing_links_by_pair: dict[tuple[str, str], MapLink] = {}
    max_selement_id = 0
    if existing_map:
        for selement in existing_map.selements:
            selementid = selement.selementid
            if selementid.isdigit():
                max_selement_id = max(max_selement_id, int(selementid))
            if selement.hostid and selementid:
                existing_host_to_selementid[selement.hostid] = selementid
            elif selementid and selement.is_image and selement.label:
                existing_label_to_image_selementid[selement.label] = selementid

        for link in existing_map.links:
            if link.selementid1 and link.selementid2:
                existing_links_by_pair[link.pair] = link

    selements: list[MapElement] = []
    selement_by_node_id: dict[str, str] = {}
    host_by_node_id: dict[str, object] = {}

    next_selement_id = max_selement_id + 1
    matched_host_count = 0
    image_node_count = 0
    image_icon_id = skipped_node_icon_id or DEFAULT_HOST_ICON_ID
    final_positions_by_device_name: dict[str, tuple[int, int]] = {}

    for node in graph.nodes:
        host = hosts_by_name.get(node.label)
        x, y = positions.get(node.node_id, (40 + next_selement_id * 20, 40 + next_selement_id * 20))
        # Persisted regardless of a current Zabbix host match: node.label is
        # always a NetBox device name, so its position is worth keeping even
        # for a node that isn't matched/rendered on this particular sync.
        if node.label:
            final_positions_by_device_name[node.label] = (x, y)

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
                MapElement(
                    selementid=selementid,
                    elementtype=ELEMENT_TYPE_IMAGE,
                    label=node.label,
                    x=x,
                    y=y,
                    iconid_off=image_icon_id,
                )
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
            MapElement(
                selementid=selementid,
                elementtype=ELEMENT_TYPE_HOST,
                label=host.name or host.host,
                x=x,
                y=y,
                iconid_off=DEFAULT_HOST_ICON_ID,
                hostid=host.hostid,
            )
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

    links: list[MapLink] = []
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
        host_pair_key = edge_data["host_pair_key"]
        hostids = edge_data["hostids"]
        link_trigger_entries: list[MapLinkTrigger] = []
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
                    link_trigger_entries.append(MapLinkTrigger(triggerid=trigger_id))
                else:
                    logger.warning(
                        "Could not match cable trigger from NetBox host_pair=%s trigger_name=%s",
                        host_pair_key,
                        trigger_name,
                    )
                    unresolved_rules.add(
                        (host_pair_key[0], host_pair_key[1], trigger_name)
                    )

        indicator_type = 0
        linktriggers: tuple[MapLinkTrigger, ...] = ()
        existing_link = existing_links_by_pair.get(pair)
        if link_trigger_entries:
            indicator_type = 1
            linktriggers = tuple(link_trigger_entries)
            logger.debug("Added %s link trigger entries to map link pair=%s", len(link_trigger_entries), pair)
        elif existing_link and existing_link.linktriggers:
            indicator_type = existing_link.indicator_type
            linktriggers = existing_link.linktriggers
            logger.debug("Preserved existing link triggers for pair=%s", pair)

        links.append(
            MapLink(
                selementid1=pair[0],
                selementid2=pair[1],
                indicator_type=indicator_type,
                linktriggers=linktriggers,
            )
        )

    label_format = None
    label_type_image = None
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
        label_format = "1"
        label_type_image = "0"
    zabbix_map = ZabbixMap(
        name=map_name,
        width=width,
        height=height,
        selements=tuple(selements),
        links=tuple(links),
        iconmapid=iconmapid,
        label_format=label_format,
        label_type_image=label_type_image,
    )
    logger.info(
        "Built map name=%s matched_hosts=%s image_nodes=%s links=%s unresolved_link_rules=%s",
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
    return (
        zabbix_map,
        matched_host_count,
        image_node_count,
        len(links),
        len(unresolved_rules),
        unresolved_details,
        final_positions_by_device_name,
    )


INVENTORY_TYPE_MAX_LENGTH = 64


def _sync_host_inventory_roles(
    zabbix: ZabbixClient,
    hosts_by_name,
    position_records: dict[str, DevicePositionRecord],
    device_names: list[str],
) -> int:
    """Write each matched device's NetBox role slug to its Zabbix host inventory "type".

    Icon maps on the Zabbix side pick the element icon from that field. Only
    hosts whose value differs are updated; a failure is logged, never fatal.
    """
    changes = []
    seen_hostids: set[str] = set()
    for device_name in device_names:
        host = hosts_by_name.get(device_name)
        record = position_records.get(device_name)
        if host is None or record is None or not record.role_slug or host.hostid in seen_hostids:
            continue
        seen_hostids.add(host.hostid)
        desired = record.role_slug[:INVENTORY_TYPE_MAX_LENGTH]
        if host.inventory_type == desired and host.inventory_mode != -1:
            continue
        logger.info(
            "Host inventory type change host=%s hostid=%s %r -> %r%s",
            host.host,
            host.hostid,
            host.inventory_type,
            desired,
            " (enabling manual inventory)" if host.inventory_mode == -1 else "",
        )
        changes.append((host, desired))

    if not changes:
        return 0
    try:
        zabbix.set_host_inventory_types(changes)
    except Exception:
        logger.exception("Failed to update Zabbix host inventory types count=%s", len(changes))
        return 0
    return len(changes)


def _live_positions_by_device_name(existing_map: ZabbixMap, zabbix: ZabbixClient) -> dict[str, tuple[int, int]]:
    """Current element positions on a Zabbix map, keyed by NetBox device name.

    Host elements are named by their Zabbix technical host name (what topology
    labels are matched against in get_hosts_by_names); image elements carry
    the NetBox device name as their label.
    """
    host_elements = [e for e in existing_map.selements if e.hostid and e.x is not None and e.y is not None]
    hosts_by_id = zabbix.get_hosts_by_ids([e.hostid for e in host_elements]) if host_elements else {}

    positions: dict[str, tuple[int, int]] = {}
    for element in host_elements:
        host = hosts_by_id.get(element.hostid)
        if host:
            positions[host.host] = (element.x, element.y)
    for element in existing_map.selements:
        if element.is_image and element.label and element.x is not None and element.y is not None:
            positions.setdefault(element.label, (element.x, element.y))
    return positions


def _persist_device_positions(
    netbox: NetBoxClient,
    map_name: str,
    position_records: dict[str, DevicePositionRecord],
    positions_by_device_name: dict[str, tuple[int, int]],
    field_name: str,
    stage: str,
) -> dict[str, DevicePositionRecord]:
    """Write changed per-map positions to NetBox; return the records with them merged in.

    The merged records are returned even when the NetBox write fails, so the
    rest of the sync still works with the freshest positions.
    """
    merged_records = dict(position_records)
    updates: list[tuple[str, dict]] = []
    for device_name, (x, y) in positions_by_device_name.items():
        record = position_records.get(device_name)
        if record is None:
            # No matching NetBox device -- nothing to write a position to.
            continue

        existing_entry = record.positions_by_map.get(map_name)
        if isinstance(existing_entry, dict):
            try:
                if int(existing_entry.get("x")) == x and int(existing_entry.get("y")) == y:
                    continue
            except (TypeError, ValueError):
                pass

        merged = {**record.positions_by_map, map_name: {"x": x, "y": y}}
        merged_records[device_name] = replace(record, positions_by_map=merged)
        updates.append((record.device_id, merged))

    if not updates:
        return merged_records

    try:
        netbox.set_device_custom_fields_bulk(updates, field_name=field_name)
        logger.info("Persisted %s %s device position(s) to NetBox map_name=%s", len(updates), stage, map_name)
    except Exception:
        # Position persistence is additive on top of the core map sync --
        # a NetBox write failure here should never fail an otherwise
        # successful Zabbix map sync.
        logger.exception("Failed to persist %s device positions to NetBox map_name=%s", stage, map_name)
    return merged_records


def sync_topology_to_zabbix_map(
    graph: TopologyGraph,
    zabbix: ZabbixClient,
    netbox: NetBoxClient,
    map_name: str,
    width: int,
    height: int,
    grid_x: int = GRID_STEP_X,
    grid_y: int = GRID_STEP_Y,
    skipped_node_mode: str = SKIPPED_NODE_MODE_SKIP,
    skipped_node_icon_id: str = "",
    position_field_name: str = DEFAULT_POSITION_FIELD,
    icon_map: str = "",
    inventory_role_sync: bool = False,
) -> SyncResult:
    topology_names = sorted({node.label for node in graph.nodes if node.label})
    logger.debug("Syncing topology labels=%s", topology_names)
    hosts = zabbix.get_hosts_by_names(topology_names)

    existing_map = zabbix.get_map_by_name(map_name)

    iconmapid = None
    if icon_map:
        iconmapid = zabbix.get_iconmap_id(icon_map)
        if iconmapid is None:
            raise ValueError(f"Zabbix icon map {icon_map!r} not found (Administration > General > Icon mapping)")

    # Snapshot where elements currently sit on the live map (possibly moved by
    # hand in Zabbix) into NetBox before anything is rewritten -- including
    # devices that are no longer part of this map's topology.
    live_positions = _live_positions_by_device_name(existing_map, zabbix) if existing_map else {}
    position_records = netbox.fetch_device_position_records(
        sorted(set(topology_names) | set(live_positions)), field_name=position_field_name
    )
    position_records = _persist_device_positions(
        netbox=netbox,
        map_name=map_name,
        position_records=position_records,
        positions_by_device_name=live_positions,
        field_name=position_field_name,
        stage="live",
    )
    stored_positions = positions_for_map(position_records, map_name)

    if inventory_role_sync:
        _sync_host_inventory_roles(zabbix, hosts, position_records, topology_names)

    (
        zabbix_map,
        matched_hosts,
        image_nodes,
        link_count,
        unresolved_link_rules,
        unresolved_details,
        final_positions_by_device_name,
    ) = build_zabbix_map(
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
        stored_positions=stored_positions,
        iconmapid=iconmapid,
    )

    created = existing_map is None

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Outgoing map payload links=%s", json.dumps(zabbix_map.to_api_payload()["links"], default=str))

    if created:
        zabbix.create_map(zabbix_map)
    else:
        zabbix.update_map(existing_map.sysmapid, zabbix_map)

    _persist_device_positions(
        netbox=netbox,
        map_name=map_name,
        position_records=position_records,
        positions_by_device_name=final_positions_by_device_name,
        field_name=position_field_name,
        stage="final",
    )

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
