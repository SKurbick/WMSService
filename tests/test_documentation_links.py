import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
DOCS_DIR = ROOT / "docs"
MARKDOWN_LINK_RE = re.compile(r"\[[^]]+]\(([^)]+)\)")


def test_internal_markdown_links_resolve() -> None:
    markdown_files = [ROOT / "README.md", *DOCS_DIR.rglob("*.md")]
    markdown_files.append(ROOT / "scripts" / "migrations" / "README.md")

    broken: list[tuple[str, str]] = []
    for document in markdown_files:
        for raw_target in MARKDOWN_LINK_RE.findall(document.read_text(encoding="utf-8")):
            target = raw_target.strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith("/"):
                continue
            if not (document.parent / target).exists():
                broken.append((str(document.relative_to(ROOT)), raw_target))

    assert broken == []


def test_docs_index_has_reading_path_and_production_audit() -> None:
    index_path = DOCS_DIR / "README.md"
    index = index_path.read_text(encoding="utf-8")
    targets = MARKDOWN_LINK_RE.findall(index)
    missing = [
        target
        for raw_target in targets
        if (target := raw_target.strip("<>").split("#", 1)[0])
        and "://" not in target
        and not target.startswith("/")
        and not (index_path.parent / target).exists()
    ]

    assert "## Быстрый маршрут" in index
    assert "## Production schema audit" in index
    assert "runtime_wms_schema.sql" in index
    assert missing == []
