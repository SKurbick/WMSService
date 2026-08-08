import re
from pathlib import Path

from fastapi.routing import APIRoute

from app.main import app


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
API_MAP_PATH = Path(__file__).parents[1] / "docs" / "current" / "api_map.md"


def _documented_operations() -> set[tuple[str, str]]:
    text = API_MAP_PATH.read_text(encoding="utf-8")
    matches = re.findall(r"(GET|POST|PUT|PATCH|DELETE) (/\S+)", text)
    return {
        (method, raw_path.split("?", 1)[0].rstrip("`.,"))
        for method, raw_path in matches
    }


def _openapi_operations() -> set[tuple[str, str]]:
    schema = app.openapi()
    return {
        (method.upper(), path)
        for path, path_item in schema["paths"].items()
        for method in path_item
        if method.upper() in HTTP_METHODS
    }


def test_api_map_matches_openapi_methods_and_paths() -> None:
    assert _documented_operations() == _openapi_operations()


def test_no_hidden_or_deprecated_application_routes() -> None:
    application_routes = [route for route in app.routes if isinstance(route, APIRoute)]

    hidden = [route.path for route in application_routes if not route.include_in_schema]
    deprecated = [route.path for route in application_routes if route.deprecated]

    assert hidden == []
    assert deprecated == []
