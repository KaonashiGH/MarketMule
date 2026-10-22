import json
from pathlib import Path

import pytest

from marketmule.core.config import PROJECT_ROOT
from marketmule.features.forums.templates import TemplateError, load_templates

FORUM_TEMPLATES = PROJECT_ROOT / "templates" / "forums"


def test_shipped_templates_are_valid() -> None:
    templates = load_templates(FORUM_TEMPLATES)
    assert set(templates) == {"bug_report", "feature_request"}


def write_template(directory: Path, **overrides: object) -> None:
    template = json.loads((FORUM_TEMPLATES / "bug_report.json").read_text(encoding="utf-8"))
    template.update(overrides)
    (directory / "test.json").write_text(json.dumps(template), encoding="utf-8")


def test_rejects_unknown_final_status(tmp_path: Path) -> None:
    write_template(tmp_path, final_statuses=["Fixed", "Exploded"])
    with pytest.raises(TemplateError, match="Exploded"):
        load_templates(tmp_path)


def test_rejects_page_over_discord_limit(tmp_path: Path) -> None:
    fields = [{"key": f"f{i}", "label": f"Field {i}"} for i in range(6)]
    write_template(tmp_path, pages=[{"name": "Too long", "title": "Too long", "fields": fields}])
    with pytest.raises(TemplateError, match="6 items"):
        load_templates(tmp_path)


def test_rejects_misspelled_placeholder(tmp_path: Path) -> None:
    write_template(tmp_path, sections=[{"heading": "Description", "body": "{descripton}"}])
    with pytest.raises(TemplateError, match="descripton"):
        load_templates(tmp_path)


def test_rejects_missing_required_key(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text('{"id": "broken"}', encoding="utf-8")
    with pytest.raises(TemplateError, match="invalid template"):
        load_templates(tmp_path)


def test_rejects_empty_folder(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="No templates"):
        load_templates(tmp_path)
