"""Tests for authenticated read-only social data status API (Task 12).

Specification:
- docs/social_data/implementation_plan.md Task 12 & §8
- GET /v1/social-data/status: authenticated, read-only
- Returns: mode, schema_version, status, recent_successful_run, platform_coverage, reason_codes
- Forbids returning post content, comment content, raw texts, cookies, API keys or credentials.
"""

import pytest
from fastapi.testclient import TestClient

from api.database import UserDB
from api.main import app
from api.services import auth_service, social_data_service


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """Generate authentication headers with a test JWT token."""
    # Test client using standard test token or bearer token
    # Create or use default test user
    from api.database import get_db_ctx
    with get_db_ctx() as db:
        user = auth_service.get_or_create_default_user(db)
        token = auth_service.create_access_token(user)
    return {"Authorization": f"Bearer {token}"}


def test_social_data_status_api_requires_auth(client):
    """GET /v1/social-data/status must require authentication."""
    # When invalid authorization header is provided
    response = client.get(
        "/v1/social-data/status",
        headers={"Authorization": "Bearer invalid_token_12345"},
    )
    assert response.status_code == 401


def test_social_data_status_api_authenticated_returns_metadata(client, auth_headers):
    """GET /v1/social-data/status returns structured metadata for authenticated users."""
    response = client.get("/v1/social-data/status", headers=auth_headers)
    assert response.status_code == 200

    data = response.json()
    assert "mode" in data
    assert "schema_version" in data
    assert "status" in data
    assert "platform_coverage" in data
    assert "reason_codes" in data or "error_codes" in data

    # Verify mode is one of valid modes
    assert data["mode"] in ("disabled", "shadow", "active")
    assert isinstance(data["platform_coverage"], dict)


def test_social_data_status_api_forbids_post_contents_and_secrets(client, auth_headers):
    """Status API must NEVER return post text, comments, cookies, or secrets."""
    response = client.get("/v1/social-data/status", headers=auth_headers)
    assert response.status_code == 200

    raw_text = response.text.lower()

    # Forbidden fields/contents
    forbidden_keys = (
        "post_text",
        "comment_text",
        "raw_text",
        "raw_posts",
        "cookie",
        "cookies",
        "secret_key",
        "api_key",
        "password",
        "xsec_token",
    )

    data = response.json()
    for key in forbidden_keys:
        assert key not in data, f"Forbidden key '{key}' found in top-level status response"

    def _check_no_forbidden_keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert str(k).lower() not in forbidden_keys, f"Forbidden key '{k}' found in nested response"
                _check_no_forbidden_keys(v)
        elif isinstance(obj, list):
            for item in obj:
                _check_no_forbidden_keys(item)

    _check_no_forbidden_keys(data)


def test_social_data_service_status_aggregation():
    """SocialDataService.get_status aggregates metadata deterministically."""
    status_data = social_data_service.get_social_data_status()

    assert isinstance(status_data, dict)
    assert "mode" in status_data
    assert "schema_version" in status_data
    assert "status" in status_data
    assert "platform_coverage" in status_data
    assert "recent_successful_run" in status_data
