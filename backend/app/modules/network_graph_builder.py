"""Shell company network graph builder.

Constructs ownership network graphs by walking VesselOwner parent_owner_id
chains (BFS upward from a vessel, then downward to find all related vessels).
Incorporates OwnerCluster membership for related-owner discovery and flags
sanctioned/SPV nodes with layered layout metadata.

Graph structure:
  Layer 0 = root companies (no parent_owner_id)
  Layer 1 = intermediary companies
  Layer 2 = leaf owners (directly own vessels)
  Layer 3 = vessels
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Default limits
DEFAULT_DEPTH = 3
DEFAULT_NODE_LIMIT = 100


def _owner_to_node(owner: Any, layer: int) -> dict[str, Any]:
    """Convert a VesselOwner ORM object to a graph node dict."""
    return {
        "id": f"owner-{owner.owner_id}",
        "type": "company",
        "label": owner.owner_name or f"Owner #{owner.owner_id}",
        "layer": layer,
        "is_sanctioned": bool(getattr(owner, "is_sanctioned", False)),
        "is_spv": bool(getattr(owner, "is_spv", False)),
        "jurisdiction": getattr(owner, "incorporation_jurisdiction", None)
        or getattr(owner, "country", None),
        "owner_id": owner.owner_id,
    }


def _vessel_to_node(vessel: Any) -> dict[str, Any]:
    """Convert a Vessel ORM object to a graph node dict."""
    return {
        "id": f"vessel-{vessel.vessel_id}",
        "type": "vessel",
        "label": getattr(vessel, "name", None) or f"Vessel #{vessel.vessel_id}",
        "layer": 3,
        "is_sanctioned": False,
        "is_spv": False,
        "jurisdiction": getattr(vessel, "flag_state", None),
        "vessel_id": vessel.vessel_id,
    }


def _walk_parents_bfs(
    db: Session,
    start_owner_ids: list[int],
    max_depth: int,
) -> dict[int, tuple[Any, int]]:
    """BFS upward through parent_owner_id chains.

    Returns dict mapping owner_id -> (owner_obj, depth_from_start).
    """
    from app.models.vessel_owner import VesselOwner

    visited: dict[int, tuple[Any, int]] = {}
    queue: deque[tuple[int, int]] = deque()

    # Seed with starting owners at depth 0
    for oid in start_owner_ids:
        owner = db.query(VesselOwner).filter(VesselOwner.owner_id == oid).first()
        if owner and oid not in visited:
            visited[oid] = (owner, 0)
            queue.append((oid, 0))

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue

        current_owner = visited[current_id][0]
        parent_id = getattr(current_owner, "parent_owner_id", None)

        if parent_id and parent_id not in visited:
            parent = (
                db.query(VesselOwner)
                .filter(VesselOwner.owner_id == parent_id)
                .first()
            )
            if parent:
                visited[parent_id] = (parent, depth + 1)
                queue.append((parent_id, depth + 1))

    return visited


def _walk_children(
    db: Session,
    owner_ids: set[int],
) -> dict[int, list[Any]]:
    """Find child owners for each owner_id (downward walk)."""
    from app.models.vessel_owner import VesselOwner

    children_map: dict[int, list[Any]] = {oid: [] for oid in owner_ids}

    if not owner_ids:
        return children_map

    children = (
        db.query(VesselOwner)
        .filter(VesselOwner.parent_owner_id.in_(owner_ids))
        .all()
    )
    for child in children:
        pid = child.parent_owner_id
        if pid in children_map:
            children_map[pid].append(child)

    return children_map


def _find_cluster_related_owners(
    db: Session,
    owner_ids: set[int],
) -> list[Any]:
    """Find owners related via OwnerCluster membership."""
    from app.models.owner_cluster_member import OwnerClusterMember

    if not owner_ids:
        return []

    # Find cluster IDs for our owners
    memberships = (
        db.query(OwnerClusterMember)
        .filter(OwnerClusterMember.owner_id.in_(owner_ids))
        .all()
    )
    cluster_ids = {m.cluster_id for m in memberships}

    if not cluster_ids:
        return []

    # Find all members of those clusters
    from app.models.vessel_owner import VesselOwner

    related_memberships = (
        db.query(OwnerClusterMember)
        .filter(
            OwnerClusterMember.cluster_id.in_(cluster_ids),
            ~OwnerClusterMember.owner_id.in_(owner_ids),
        )
        .all()
    )
    related_owner_ids = {m.owner_id for m in related_memberships}

    if not related_owner_ids:
        return []

    return (
        db.query(VesselOwner)
        .filter(VesselOwner.owner_id.in_(related_owner_ids))
        .all()
    )


def _find_vessels_for_owners(
    db: Session,
    owner_ids: set[int],
) -> list[Any]:
    """Find vessels owned by the given owners."""
    from app.models.vessel import Vessel
    from app.models.vessel_owner import VesselOwner

    if not owner_ids:
        return []

    vessel_ids_rows = (
        db.query(VesselOwner.vessel_id)
        .filter(VesselOwner.owner_id.in_(owner_ids))
        .distinct()
        .all()
    )
    vessel_ids = {r[0] for r in vessel_ids_rows}

    if not vessel_ids:
        return []

    return db.query(Vessel).filter(Vessel.vessel_id.in_(vessel_ids)).all()


def _assign_layer(owner: Any, all_owners: dict[int, tuple[Any, int]], max_depth: int) -> int:
    """Assign layer based on position in ownership hierarchy.

    Layer 0 = root (no parent or at max depth)
    Layer 1 = intermediary (has both parent and children)
    Layer 2 = leaf owner (directly owns vessels, has parent but no children in graph)
    """
    has_parent = getattr(owner, "parent_owner_id", None) is not None
    parent_in_graph = (
        has_parent and getattr(owner, "parent_owner_id", None) in all_owners
    )

    # Check if any owner in graph lists this as parent
    has_children_in_graph = any(
        getattr(o, "parent_owner_id", None) == owner.owner_id
        for o, _ in all_owners.values()
    )

    if not parent_in_graph and has_children_in_graph:
        return 0  # root
    elif parent_in_graph and has_children_in_graph:
        return 1  # intermediary
    else:
        return 2  # leaf


def _find_sanctions_paths(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[list[str]]:
    """BFS from each sanctioned node to find connected paths."""
    sanctioned_ids = {n["id"] for n in nodes if n.get("is_sanctioned")}
    if not sanctioned_ids:
        return []

    # Build adjacency list (undirected for path finding)
    adj: dict[str, list[str]] = {}
    for node in nodes:
        adj[node["id"]] = []
    for edge in edges:
        src, tgt = edge["source"], edge["target"]
        if src in adj:
            adj[src].append(tgt)
        if tgt in adj:
            adj[tgt].append(src)

    paths: list[list[str]] = []

    for start_id in sanctioned_ids:
        visited: set[str] = set()
        queue: deque[list[str]] = deque([[start_id]])

        while queue:
            path = queue.popleft()
            current = path[-1]

            if current in visited:
                continue
            visited.add(current)

            # If we reached another node (not start), record the path
            if len(path) > 1:
                paths.append(path[:])

            for neighbor in adj.get(current, []):
                if neighbor not in visited:
                    queue.append(path + [neighbor])

    return paths


def build_ownership_network(
    db: Session,
    vessel_id: int | None = None,
    depth: int = DEFAULT_DEPTH,
    limit: int = DEFAULT_NODE_LIMIT,
    sanctioned_only: bool = False,
    spv_only: bool = False,
    jurisdiction: str | None = None,
) -> dict[str, Any]:
    """Build an ownership network graph.

    Args:
        db: Database session.
        vessel_id: Optional vessel ID to center the graph on.
        depth: Maximum depth for parent chain BFS (default 3).
        limit: Maximum number of nodes to return (default 100).
        sanctioned_only: If True, only include sanctioned nodes and their neighbors.
        spv_only: If True, only include SPV nodes and their neighbors.
        jurisdiction: Filter to specific jurisdiction code.

    Returns:
        Dict with nodes, edges, sanctions_paths, and stats.
    """
    from app.models.vessel import Vessel
    from app.models.vessel_owner import VesselOwner

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_node_ids: set[str] = set()

    # Step 1: Find starting owners
    if vessel_id is not None:
        start_owners = (
            db.query(VesselOwner)
            .filter(VesselOwner.vessel_id == vessel_id)
            .all()
        )
        if not start_owners:
            return {
                "nodes": [],
                "edges": [],
                "sanctions_paths": [],
                "stats": {
                    "total_nodes": 0,
                    "total_edges": 0,
                    "max_depth": 0,
                    "sanctioned_count": 0,
                    "spv_count": 0,
                },
            }
        start_owner_ids = [o.owner_id for o in start_owners]

        # Add the source vessel node
        vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
        if vessel:
            vnode = _vessel_to_node(vessel)
            nodes.append(vnode)
            seen_node_ids.add(vnode["id"])
    else:
        # Fleet-wide: start from all owners, apply filters
        query = db.query(VesselOwner)
        if sanctioned_only:
            query = query.filter(VesselOwner.is_sanctioned.is_(True))
        if spv_only:
            query = query.filter(VesselOwner.is_spv.is_(True))
        if jurisdiction:
            query = query.filter(
                VesselOwner.incorporation_jurisdiction == jurisdiction.upper()
            )
        start_owners = query.limit(limit).all()
        start_owner_ids = [o.owner_id for o in start_owners]

    if not start_owner_ids:
        return {
            "nodes": [],
            "edges": [],
            "sanctions_paths": [],
            "stats": {
                "total_nodes": 0,
                "total_edges": 0,
                "max_depth": 0,
                "sanctioned_count": 0,
                "spv_count": 0,
            },
        }

    # Step 2: BFS upward through parent chains
    all_owners = _walk_parents_bfs(db, start_owner_ids, depth)

    # Step 3: Find cluster-related owners
    owner_id_set = set(all_owners.keys())
    cluster_related = _find_cluster_related_owners(db, owner_id_set)
    for related_owner in cluster_related:
        if related_owner.owner_id not in all_owners:
            all_owners[related_owner.owner_id] = (related_owner, 1)

    # Step 4: Walk downward to find child owners
    children_map = _walk_children(db, set(all_owners.keys()))
    for _parent_id, children in children_map.items():
        for child in children:
            if child.owner_id not in all_owners:
                all_owners[child.owner_id] = (child, 0)

    # Step 5: Apply filters
    filtered_owners: dict[int, tuple[Any, int]] = {}
    for oid, (owner, d) in all_owners.items():
        if sanctioned_only and not getattr(owner, "is_sanctioned", False):
            continue
        if spv_only and not getattr(owner, "is_spv", False):
            continue
        if jurisdiction:
            owner_jur = getattr(owner, "incorporation_jurisdiction", None) or ""
            if owner_jur.upper() != jurisdiction.upper():
                continue
        filtered_owners[oid] = (owner, d)

    # Step 6: Build owner nodes with layer assignment
    max_depth_seen = 0
    for _oid, (owner, d) in filtered_owners.items():
        if len(nodes) >= limit:
            break
        layer = _assign_layer(owner, filtered_owners, depth)
        node = _owner_to_node(owner, layer)
        if node["id"] not in seen_node_ids:
            nodes.append(node)
            seen_node_ids.add(node["id"])
            max_depth_seen = max(max_depth_seen, d)

    # Step 7: Find and add vessel nodes
    all_owner_ids = {oid for oid in filtered_owners}
    vessels = _find_vessels_for_owners(db, all_owner_ids)
    for vessel in vessels:
        if len(nodes) >= limit:
            break
        vnode = _vessel_to_node(vessel)
        if vnode["id"] not in seen_node_ids:
            nodes.append(vnode)
            seen_node_ids.add(vnode["id"])

    # Step 8: Build edges
    # Owner -> parent edges
    for oid, (owner, _) in filtered_owners.items():
        source_id = f"owner-{oid}"
        if source_id not in seen_node_ids:
            continue
        parent_id = getattr(owner, "parent_owner_id", None)
        if parent_id:
            target_id = f"owner-{parent_id}"
            if target_id in seen_node_ids:
                edges.append({
                    "source": source_id,
                    "target": target_id,
                    "relationship": getattr(owner, "ownership_type", None) or "subsidiary",
                })

    # Owner -> vessel edges
    vessel_owner_links = (
        db.query(VesselOwner)
        .filter(VesselOwner.owner_id.in_(all_owner_ids))
        .all()
    )
    for link in vessel_owner_links:
        owner_node_id = f"owner-{link.owner_id}"
        vessel_node_id = f"vessel-{link.vessel_id}"
        if owner_node_id in seen_node_ids and vessel_node_id in seen_node_ids:
            edges.append({
                "source": owner_node_id,
                "target": vessel_node_id,
                "relationship": "owns",
            })

    # Cluster relationship edges
    from app.models.owner_cluster_member import OwnerClusterMember

    owner_ids_in_graph = {
        oid for oid in filtered_owners if f"owner-{oid}" in seen_node_ids
    }
    if owner_ids_in_graph:
        memberships = (
            db.query(OwnerClusterMember)
            .filter(OwnerClusterMember.owner_id.in_(owner_ids_in_graph))
            .all()
        )
        # Group by cluster
        cluster_members: dict[int, list[int]] = {}
        for m in memberships:
            cluster_members.setdefault(m.cluster_id, []).append(m.owner_id)

        # Create edges between members of same cluster
        for _cid, members in cluster_members.items():
            for i, a in enumerate(members):
                for b in members[i + 1 :]:
                    a_id = f"owner-{a}"
                    b_id = f"owner-{b}"
                    if a_id in seen_node_ids and b_id in seen_node_ids:
                        edges.append({
                            "source": a_id,
                            "target": b_id,
                            "relationship": "cluster_related",
                        })

    # Step 9: Sanctions paths
    sanctions_paths = _find_sanctions_paths(nodes, edges)

    # Step 10: Stats
    sanctioned_count = sum(1 for n in nodes if n.get("is_sanctioned"))
    spv_count = sum(1 for n in nodes if n.get("is_spv"))

    return {
        "nodes": nodes,
        "edges": edges,
        "sanctions_paths": sanctions_paths,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "max_depth": max_depth_seen,
            "sanctioned_count": sanctioned_count,
            "spv_count": spv_count,
        },
    }
