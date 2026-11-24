"""
InsightIDR Context Manager

Read-only context lookups that support triage, built on the REST transport
in insightidr_client.py:

- Entities (InsightIDR API v1, stable): users, assets, accounts, and local
  accounts — "who is this user / what is this host" without writing LEQL.
- Health metrics (InsightIDR API v2): state of agents, collectors, event
  sources, network sensors, and other data-collection components.

Neither API family takes the Investigations `Accept-version` preview header.

API reference: rapid7-insightidr-skill/references/insightidr-api-v1.json and
investigations-api-v2.json (official Rapid7 OpenAPI specs).

Author: rozumeyroman@gmail.com
"""

from typing import Any, Dict, List, Optional

from .insightidr_client import send_idr_request

# entity_type -> (v1 path, {searchable field: operator}).
#
# Searchable fields come from each _search operation's field table in the v1
# spec. The spec allows EQUALS/CONTAINS/IN but does not document which
# operators each field accepts, so strings default to CONTAINS (the useful
# operator for "find by partial name"), RRN references use EQUALS, and the
# boolean `disabled` uses EQUALS. `authentication_service` is an enum
# server-side (verified live 2026-11-24: CONTAINS returns 400 "non-String
# field"), so it is EQUALS-only — values seen: ACTIVE_DIRECTORY, AZURE_AD,
# O365, DUO.
ENTITY_TYPES: Dict[str, Dict[str, Any]] = {
    "users": {
        "path": "/idr/v1/users",
        "fields": {"name": "CONTAINS", "first_name": "CONTAINS", "last_name": "CONTAINS", "domain": "CONTAINS"},
    },
    "assets": {
        "path": "/idr/v1/assets",
        "fields": {"name": "CONTAINS"},
    },
    "accounts": {
        "path": "/idr/v1/accounts",
        "fields": {
            "name": "CONTAINS",
            "domain": "CONTAINS",
            "authentication_service": "EQUALS",
            "disabled": "EQUALS",
            "user.rrn": "EQUALS",
            "user.name": "CONTAINS",
        },
    },
    "local_accounts": {
        "path": "/idr/v1/assets/local-accounts",
        "fields": {
            "name": "CONTAINS",
            "asset.rrn": "EQUALS",
            "asset.name": "CONTAINS",
            "user.rrn": "EQUALS",
            "user.name": "CONTAINS",
        },
    },
}

# resourceTypes values accepted by GET /idr/v2/health-metrics. Taken from the
# live API's own 400 message (2026-11-24), NOT the spec: the spec's examples
# say "sensors", but the API only accepts "network_sensors".
HEALTH_RESOURCE_TYPES = (
    "agent",
    "collectors",
    "data_exporters",
    "event_sources",
    "honeypots",
    "network_sensors",
    "orchestrator",
    "scan_engines",
)

_HEALTH_METRICS_PATH = "/idr/v2/health-metrics"


def _entity_spec(entity_type: str) -> Dict[str, Any]:
    spec = ENTITY_TYPES.get(entity_type)
    if spec is None:
        raise ValueError(f"Unknown entity type: {entity_type}. Valid types: {', '.join(ENTITY_TYPES)}")
    return spec


def search_entities(
    config: Dict[str, str],
    entity_type: str,
    criteria: Dict[str, Any],
    exact: bool = False,
    size: int = 20,
    index: int = 0,
) -> Dict[str, Any]:
    """Search users, assets, accounts, or local accounts (POST /idr/v1/{type}/_search).

    Args:
        config: Config dict from load_config() (needs api_key, idr_base).
        entity_type: One of ENTITY_TYPES.
        criteria: {field: value}; all criteria must match. Empty values are
            dropped. Fields must be searchable for the given entity type.
        exact: Use EQUALS instead of CONTAINS for string fields.
        size: Page size, 1-100.
        index: 0-based page index.

    Returns:
        The raw API response: {"data": [...], "metadata": {...}}.

    Raises:
        ValueError: On an unknown entity type or a field that isn't
            searchable for it — checked before any API call.
    """
    spec = _entity_spec(entity_type)
    fields = spec["fields"]

    search: List[Dict[str, Any]] = []
    for field, value in criteria.items():
        if value is None or value == "":
            continue
        if field not in fields:
            raise ValueError(f"Field {field!r} is not searchable for {entity_type}. Valid fields: {', '.join(fields)}")
        operator = "EQUALS" if exact else fields[field]
        search.append({"field": field, "operator": operator, "value": value})

    return send_idr_request(
        "POST",
        config["idr_base"],
        f"{spec['path']}/_search",
        config["api_key"],
        params={"size": size, "index": index},
        json_body={"search": search},
    )


def get_entity(config: Dict[str, str], entity_type: str, rrn: str) -> Dict[str, Any]:
    """Get a single user, asset, account, or local account by RRN."""
    spec = _entity_spec(entity_type)
    return send_idr_request("GET", config["idr_base"], f"{spec['path']}/{rrn}", config["api_key"])


def get_health_metrics(
    config: Dict[str, str],
    resource_types: Optional[str] = None,
    with_epm: bool = False,
    size: int = 100,
    index: int = 0,
) -> Dict[str, Any]:
    """Get health metrics for the org's data-collection components (GET /idr/v2/health-metrics).

    A 207 (Multi-Status) response is a partial success: the body is still a
    normal page, and metadata may additionally carry `failed_to_load` and
    `unlicensed_resource_types`.

    Args:
        config: Config dict from load_config() (needs api_key, idr_base).
        resource_types: Comma-separated subset of HEALTH_RESOURCE_TYPES, or
            None for all.
        with_epm: Include events-per-minute for event sources (slower call).
        size: Page size, 1-100.
        index: 0-based page index.

    Raises:
        ValueError: On a resource type not in HEALTH_RESOURCE_TYPES.
    """
    params: Dict[str, Any] = {"size": size, "index": index}
    if resource_types:
        requested = [t.strip() for t in resource_types.split(",") if t.strip()]
        unknown = [t for t in requested if t not in HEALTH_RESOURCE_TYPES]
        if unknown:
            raise ValueError(
                f"Unknown resource type(s): {', '.join(unknown)}. Valid types: {', '.join(HEALTH_RESOURCE_TYPES)}"
            )
        params["resourceTypes"] = ",".join(requested)
    if with_epm:
        params["withEpm"] = "true"

    return send_idr_request("GET", config["idr_base"], _HEALTH_METRICS_PATH, config["api_key"], params=params)
