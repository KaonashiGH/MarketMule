from marketmule.core.config import PROJECT_ROOT
from marketmule.features.forums.samples import sample_answers, sample_text
from marketmule.features.forums.templates import load_templates

TEMPLATES = load_templates(PROJECT_ROOT / "templates" / "forums")


def test_answers_come_from_the_placeholders() -> None:
    assert sample_text("e.g. AK-47 | Case Hardened", "Item") == "AK-47 | Case Hardened"
    assert sample_text("e.g., Field-Tested", "Wear") == "Field-Tested"
    assert sample_text(None, "Browser & OS") == "Test answer for: Browser & OS"


def test_every_form_gets_a_full_set_of_answers() -> None:
    for template in TEMPLATES.values():
        text, choices = sample_answers(template)
        for field in template.fields:
            if field.kind == "text":
                assert text[field.key], (template.id, field.key)
                assert not field.max_length or len(text[field.key]) <= field.max_length
            elif field.kind == "select":
                assert 1 <= len(choices[field.key]) <= field.max_values
