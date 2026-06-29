"""Regression tests for operator API admin authorization boundaries."""

from __future__ import annotations

from fastapi.routing import APIRoute

from netengine.api.auth import require_admin
from netengine.api.routes import router


def _dependency_calls(route: APIRoute) -> set[object]:
    return {dependency.call for dependency in route.dependant.dependencies}


def test_all_state_changing_operator_routes_require_admin() -> None:
    """Every POST/PUT/DELETE /api/v1 route must use the admin dependency."""
    mutating_routes = [
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/api/v1")
        and route.methods
        and route.methods & {"POST", "PUT", "DELETE"}
    ]

    assert mutating_routes, "expected state-changing operator routes to be registered"

    missing_admin = sorted(
        f"{','.join(sorted(route.methods or []))} {route.path}"
        for route in mutating_routes
        if require_admin not in _dependency_calls(route)
    )

    assert missing_admin == []
