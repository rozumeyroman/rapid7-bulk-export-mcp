"""
Tool-level tests for the InsightIDR triage-context MCP tools in mcp_server.py:
get_investigations' time/sort params, search_investigations,
search_idr_entities, get_idr_entity, and get_idr_health_metrics.

The manager functions are stubbed, so these cover argument mapping and
output formatting — the HTTP layer is covered by the manager tests.

Author: rozumeyroman@gmail.com
"""

import pytest

from src import mcp_server

CONFIG = {"api_key": "test-api-key", "idr_base": "https://eu.api.insight.rapid7.com"}

INVESTIGATION = {
    "rrn": "rrn:rapid7:eu:investigation:1",
    "title": "Suspicious Authentication",
    "priority": "HIGH",
    "status": "OPEN",
    "disposition": "UNKNOWN",
    "assignee": {"name": "Ana", "email": "ana@example.com"},
    "created_time": "2026-11-01T00:00:00Z",
}


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(mcp_server, "load_config", lambda: CONFIG)


class _Recorder:
    """Stand-in for a manager function: records kwargs, returns a canned response."""

    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.args = None
        self.kwargs = None

    def __call__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        if self.exc:
            raise self.exc
        return self.response


class TestGetInvestigationsTimeAndSort:
    def test_passes_new_params_and_empty_as_none(self, monkeypatch):
        rec = _Recorder({"data": [INVESTIGATION], "metadata": {"total_data": 1}})
        monkeypatch.setattr(mcp_server, "idr_list_investigations", rec)

        out = mcp_server.get_investigations(start_time="2026-01-01T00:00:00Z", sort="created_time,DESC")

        assert rec.kwargs["start_time"] == "2026-01-01T00:00:00Z"
        assert rec.kwargs["end_time"] is None
        assert rec.kwargs["sort"] == "created_time,DESC"
        assert "Suspicious Authentication" in out

    def test_invalid_sort_surfaces_as_error_text(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "idr_list_investigations", _Recorder(exc=ValueError("Invalid sort: 'x'")))
        assert mcp_server.get_investigations(sort="x").startswith("✗ Error listing investigations")


class TestSearchInvestigationsTool:
    def test_maps_args_to_spec_fields(self, monkeypatch):
        rec = _Recorder({"data": [INVESTIGATION], "metadata": {"total_data": 1}})
        monkeypatch.setattr(mcp_server, "idr_search_investigations", rec)

        out = mcp_server.search_investigations(actor_asset_hostname="WS-042", mitre_technique="t1110 ", status="open")

        criteria = rec.args[1]
        assert criteria["actor_asset_hostname"] == "WS-042"
        assert criteria["alert_mitre_t_codes"] == "T1110"
        assert criteria["status"] == "OPEN"
        assert criteria["title"] == ""
        # Newest first by default.
        assert rec.kwargs["sort_field"] == "created_time"
        assert rec.kwargs["sort_order"] == "DESC"
        assert "rrn:rapid7:eu:investigation:1" in out

    def test_no_results(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "idr_search_investigations", _Recorder({"data": [], "metadata": {}}))
        assert mcp_server.search_investigations(title_contains="nothing") == (
            "No investigations found matching the given filters."
        )


class TestSearchIdrEntities:
    def test_formats_accounts_with_linked_user(self, monkeypatch):
        rec = _Recorder(
            {
                "data": [
                    {
                        "rrn": "rrn:acct:1",
                        "name": "jdoe",
                        "domain": "CORP",
                        "disabled": False,
                        "authentication_service": "ACTIVE_DIRECTORY",
                        "user": {"rrn": "rrn:user:1", "name": "Jane Doe"},
                    }
                ],
                "metadata": {"total_data": 1},
            }
        )
        monkeypatch.setattr(mcp_server, "idr_search_entities", rec)

        out = mcp_server.search_idr_entities("accounts", name="jdoe", disabled="False")

        assert rec.args[1] == "accounts"
        assert rec.args[2] == {"name": "jdoe", "disabled": False}
        assert "rrn: rrn:acct:1" in out
        assert "disabled: False" in out
        assert "user: Jane Doe (rrn:user:1)" in out

    def test_authentication_service_is_upper_cased(self, monkeypatch):
        rec = _Recorder({"data": [], "metadata": {}})
        monkeypatch.setattr(mcp_server, "idr_search_entities", rec)

        mcp_server.search_idr_entities("accounts", authentication_service=" azure_ad ")

        assert rec.args[2] == {"authentication_service": "AZURE_AD"}

    def test_unknown_type_is_rejected_before_api(self, monkeypatch):
        rec = _Recorder({})
        monkeypatch.setattr(mcp_server, "idr_search_entities", rec)

        out = mcp_server.search_idr_entities("hosts", name="x")

        assert out.startswith("✗ Unknown entity_type")
        assert rec.args is None

    def test_bad_disabled_flag(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "idr_search_entities", _Recorder({}))
        assert mcp_server.search_idr_entities("accounts", disabled="maybe").startswith("✗ disabled must be")

    def test_no_results(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "idr_search_entities", _Recorder({"data": [], "metadata": {}}))
        assert mcp_server.search_idr_entities("assets", name="nope") == "No assets found matching the given criteria."


class TestGetIdrEntity:
    def test_returns_json(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "idr_get_entity", _Recorder({"rrn": "rrn:asset:1", "name": "WS-042"}))
        out = mcp_server.get_idr_entity("assets", "rrn:asset:1")
        assert '"name": "WS-042"' in out

    def test_unknown_type(self):
        assert mcp_server.get_idr_entity("hosts", "rrn:x").startswith("✗ Unknown entity_type")


# Per-type responses, shaped like the live API (2026-11-24): records carry no
# type field, and RRN formats differ per product — an event source RRN even
# contains ":collector:", so the tool must label by the requested type.
HEALTH_BY_TYPE = {
    "agent": {
        "data": [{"rrn": "rrn:agents:eu:org:status:summary", "online": 25, "offline": 5, "stale": 3, "total": 33}],
        "metadata": {"total_pages": 1},
    },
    "collectors": {
        "data": [
            {
                "rrn": "rrn:collection:eu:org:collector:a",
                "name": "Main Collector",
                "state": "RUNNING",
                "last_active": "2026-11-24T10:00:00Z",
            }
        ],
        "metadata": {"total_pages": 1},
    },
    "event_sources": {
        "data": [
            {
                "rrn": "rrn:collection:eu:org:eventsource:collector:s1",
                "name": "Firewall",
                "state": "RUNNING",
                "last_active": "2026-11-20T10:00:00Z",
                "issue": {"severity": "ERROR", "message": "No events received"},
            }
        ],
        "metadata": {"total_pages": 1},
    },
    "orchestrator": {
        "data": [{"rrn": "rrn:icon:eu:org:orchestrator:o1", "name": "Orch", "state": "ERROR"}],
        "metadata": {"total_pages": 1},
    },
    "scan_engines": {"data": [], "metadata": {"unlicensed_resource_types": ["SCAN_ENGINES"]}},
}


class _HealthFake:
    """Answers get_health_metrics per requested type; records the calls."""

    def __init__(self, fail_type=None):
        self.calls = []
        self.fail_type = fail_type

    def __call__(self, config, resource_types=None, with_epm=False):
        self.calls.append((resource_types, with_epm))
        if resource_types == self.fail_type:
            raise RuntimeError("boom")
        return HEALTH_BY_TYPE.get(resource_types, {"data": [], "metadata": {}})


class TestGetIdrHealthMetrics:
    def test_queries_each_type_and_labels_by_requested_type(self, monkeypatch):
        fake = _HealthFake()
        monkeypatch.setattr(mcp_server, "idr_get_health_metrics", fake)

        out = mcp_server.get_idr_health_metrics()

        assert [t for t, _ in fake.calls] == list(mcp_server.IDR_HEALTH_RESOURCE_TYPES)
        assert "agent (1):" in out
        assert "total 33, online 25, offline 5, stale 3" in out
        assert "collectors (1):" in out
        assert "Main Collector: state RUNNING" in out
        # The event source is listed under event_sources, despite ":collector:" in its RRN.
        assert "event_sources (1):" in out
        assert "issue [ERROR]: No events received" in out
        assert "Not licensed for this org: SCAN_ENGINES" in out

    def test_problems_only_filters_healthy_components(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "idr_get_health_metrics", _HealthFake())

        out = mcp_server.get_idr_health_metrics(problems_only=True)

        assert "Main Collector" not in out
        assert "Firewall" in out
        assert "Orch: state ERROR" in out
        # Agents with offline/stale members count as a problem.
        assert "agent (1):" in out

    def test_one_failing_type_does_not_hide_the_rest(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "idr_get_health_metrics", _HealthFake(fail_type="collectors"))

        out = mcp_server.get_idr_health_metrics()

        assert "⚠ Errors: collectors: boom" in out
        assert "event_sources (1):" in out

    def test_requested_subset_and_epm(self, monkeypatch):
        fake = _HealthFake()
        monkeypatch.setattr(mcp_server, "idr_get_health_metrics", fake)

        out = mcp_server.get_idr_health_metrics(resource_types="network_sensors", include_epm=True)

        assert fake.calls == [("network_sensors", True)]
        assert out == "No health metrics returned for this organization."

    def test_unknown_type_rejected_before_api(self, monkeypatch):
        fake = _HealthFake()
        monkeypatch.setattr(mcp_server, "idr_get_health_metrics", fake)

        out = mcp_server.get_idr_health_metrics(resource_types="sensors")

        assert out.startswith("✗ Unknown resource type(s): sensors")
        assert fake.calls == []


class TestCloseInvestigationDisposition:
    OPEN_INVESTIGATION = {**INVESTIGATION, "status": "OPEN", "disposition": "UNKNOWN"}

    def test_security_test_lowercase_is_normalized_in_preview(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "idr_get_investigation", _Recorder(self.OPEN_INVESTIGATION))
        closer = _Recorder({})
        monkeypatch.setattr(mcp_server, "idr_close_investigation", closer)

        out = mcp_server.close_investigation("inv-1", " security_test ")

        assert "disposition 'SECURITY_TEST'" in out
        assert 'disposition="SECURITY_TEST", confirm=True' in out
        # Preview only — nothing is closed without confirm=True.
        assert closer.args is None

    def test_confirmed_close_sends_normalized_disposition(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "idr_get_investigation", _Recorder(self.OPEN_INVESTIGATION))
        closer = _Recorder({"status": "CLOSED", "disposition": "SECURITY_TEST"})
        monkeypatch.setattr(mcp_server, "idr_close_investigation", closer)

        mcp_server.close_investigation("inv-1", "Security_Test", confirm=True)

        assert closer.args[2] == "SECURITY_TEST"

    def test_unknown_is_rejected(self, monkeypatch):
        getter = _Recorder(self.OPEN_INVESTIGATION)
        monkeypatch.setattr(mcp_server, "idr_get_investigation", getter)

        out = mcp_server.close_investigation("inv-1", "UNKNOWN")

        assert out.startswith("✗ Invalid disposition")
        assert getter.args is None
