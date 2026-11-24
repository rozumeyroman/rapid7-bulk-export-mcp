"""
Unit tests for the InsightIDR context manager module: entity search/lookup
(users, assets, accounts, local accounts — API v1) and health metrics (v2).

Author: rozumeyroman@gmail.com
"""

import json

import pytest
import responses

from src.insightidr_context_manager import (
    ENTITY_TYPES,
    get_entity,
    get_health_metrics,
    search_entities,
)

CONFIG = {"api_key": "test-api-key", "idr_base": "https://eu.api.insight.rapid7.com"}


class TestSearchEntities:
    @pytest.mark.parametrize(
        "entity_type,path",
        [
            ("users", "/idr/v1/users/_search"),
            ("assets", "/idr/v1/assets/_search"),
            ("accounts", "/idr/v1/accounts/_search"),
            ("local_accounts", "/idr/v1/assets/local-accounts/_search"),
        ],
    )
    @responses.activate
    def test_posts_to_the_right_search_path(self, entity_type, path):
        responses.add(responses.POST, f"{CONFIG['idr_base']}{path}", json={"data": [], "metadata": {}}, status=200)

        search_entities(CONFIG, entity_type, {"name": "x"})

        call = responses.calls[0].request
        assert call.url.startswith(f"{CONFIG['idr_base']}{path}")
        # v1 entity APIs are stable — no preview header.
        assert "Accept-version" not in call.headers

    @responses.activate
    def test_default_operators_and_exact_override(self):
        responses.add(
            responses.POST,
            f"{CONFIG['idr_base']}/idr/v1/accounts/_search",
            json={"data": [], "metadata": {}},
            status=200,
        )

        search_entities(CONFIG, "accounts", {"name": "jdoe", "disabled": True, "domain": ""}, size=5)
        search_entities(CONFIG, "accounts", {"name": "jdoe"}, exact=True)

        first = json.loads(responses.calls[0].request.body)
        assert first["search"] == [
            {"field": "name", "operator": "CONTAINS", "value": "jdoe"},
            {"field": "disabled", "operator": "EQUALS", "value": True},
        ]
        assert "size=5" in responses.calls[0].request.url
        second = json.loads(responses.calls[1].request.body)
        assert second["search"] == [{"field": "name", "operator": "EQUALS", "value": "jdoe"}]

    def test_unknown_entity_type_raises(self):
        with pytest.raises(ValueError, match="Unknown entity type"):
            search_entities(CONFIG, "hosts", {})

    def test_field_not_searchable_for_type_raises(self):
        with pytest.raises(ValueError, match="not searchable for assets"):
            search_entities(CONFIG, "assets", {"domain": "corp"})

    @responses.activate
    def test_authentication_service_is_equals_only(self):
        # Live API rejects CONTAINS on this enum-backed field with 400.
        responses.add(
            responses.POST,
            f"{CONFIG['idr_base']}/idr/v1/accounts/_search",
            json={"data": [], "metadata": {}},
            status=200,
        )

        search_entities(CONFIG, "accounts", {"authentication_service": "AZURE_AD"})

        body = json.loads(responses.calls[0].request.body)
        assert body["search"] == [{"field": "authentication_service", "operator": "EQUALS", "value": "AZURE_AD"}]

    def test_sensors_is_not_a_valid_health_type(self):
        # The spec's examples say "sensors"; the live API only accepts "network_sensors".
        with pytest.raises(ValueError, match="Unknown resource type"):
            get_health_metrics(CONFIG, resource_types="sensors")

    def test_every_type_supports_name(self):
        assert all("name" in spec["fields"] for spec in ENTITY_TYPES.values())


class TestGetEntity:
    @responses.activate
    def test_fetches_by_rrn(self):
        rrn = "rrn:rapid7:eu:customer:1:user:abc"
        responses.add(
            responses.GET,
            f"{CONFIG['idr_base']}/idr/v1/users/{rrn}",
            json={"rrn": rrn, "name": "Jane Doe"},
            status=200,
        )

        assert get_entity(CONFIG, "users", rrn)["name"] == "Jane Doe"

    def test_unknown_entity_type_raises(self):
        with pytest.raises(ValueError):
            get_entity(CONFIG, "hosts", "rrn:x")


class TestGetHealthMetrics:
    @responses.activate
    def test_default_call(self):
        responses.add(
            responses.GET,
            f"{CONFIG['idr_base']}/idr/v2/health-metrics",
            json={"data": [], "metadata": {}},
            status=200,
        )

        get_health_metrics(CONFIG)

        url = responses.calls[0].request.url
        assert "size=100" in url
        assert "resourceTypes" not in url
        assert "withEpm" not in url

    @responses.activate
    def test_filters_and_epm(self):
        responses.add(
            responses.GET,
            f"{CONFIG['idr_base']}/idr/v2/health-metrics",
            json={"data": [], "metadata": {}},
            status=200,
        )

        get_health_metrics(CONFIG, resource_types="collectors, event_sources", with_epm=True)

        url = responses.calls[0].request.url
        assert "resourceTypes=collectors%2Cevent_sources" in url
        assert "withEpm=true" in url

    @responses.activate
    def test_multi_status_207_is_returned_not_raised(self):
        responses.add(
            responses.GET,
            f"{CONFIG['idr_base']}/idr/v2/health-metrics",
            json={"data": [], "metadata": {"failed_to_load": ["network_sensors"]}},
            status=207,
        )

        assert get_health_metrics(CONFIG)["metadata"]["failed_to_load"] == ["network_sensors"]

    def test_unknown_resource_type_raises(self):
        with pytest.raises(ValueError, match="Unknown resource type"):
            get_health_metrics(CONFIG, resource_types="agent,toasters")
