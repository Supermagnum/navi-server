"""CoMaps leaves that are not client-routable (no Navi sidecar).

World.mwm and WorldCoasts.mwm are overlay / coastline datasets without a
per-country road network usable for Navi wetland/elevation routing. They
inflated elevation RSS (World peaked ~15 GiB) and must be excluded from
sidecar processing. Clients still download the MWMs from CoMaps; the catalog
marks them `sidecar: null` / `no_sidecar: true`.
"""
from __future__ import annotations

# Exact leaf ids from CoMaps countries.txt (no country routing graph).
NON_ROUTABLE_LEAVES: frozenset[str] = frozenset(
    {
        "World",
        "WorldCoasts",
    }
)


def is_non_routable_leaf(leaf_id: str) -> bool:
    return leaf_id in NON_ROUTABLE_LEAVES


def routable_leaves(leaves: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    return [leaf for leaf in leaves if not is_non_routable_leaf(leaf)]


def non_routable_catalog_entry(leaf_id: str, discovery_leaf: dict | None = None) -> dict:
    """Catalog leaf entry: present for discovery, explicitly no sidecar."""
    meta = discovery_leaf or {}
    return {
        "id": leaf_id,
        "no_sidecar": True,
        "sidecar": None,
        "sidecar_url": None,
        "sidecar_bytes": None,
        "sidecar_sha256": None,
        "shared": False,
        "comaps_file": f"{leaf_id}.mwm",
        "comaps_bytes": meta.get("size") or meta.get("comaps_bytes"),
        "comaps_sha1_base64": meta.get("sha1_base64") or meta.get("comaps_sha1_base64"),
        "note": "Non-routable CoMaps overlay leaf; Navi does not publish a sidecar.",
    }
