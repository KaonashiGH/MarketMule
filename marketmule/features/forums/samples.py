"""Made-up answers for /forums testpost, so testing a form doesn't mean typing it out every time.

Each answer comes from the question's own placeholder ("e.g. AK-47 | Case Hardened" becomes
"AK-47 | Case Hardened"), so the test post shows exactly the examples members see in the form.
"""

from __future__ import annotations

from .templates import Template

EXAMPLE_PREFIXES = ("e.g. ", "e.g., ", "e.g.")


def sample_answers(template: Template) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Return (typed answers, dropdown picks) that fill in every question of the form."""
    text: dict[str, str] = {}
    choices: dict[str, list[str]] = {}
    for field in template.fields:
        if field.kind == "select":
            choices[field.key] = list(field.options[: field.max_values])
        elif field.kind == "text":
            answer = sample_text(field.placeholder, field.label)
            text[field.key] = answer[: field.max_length] if field.max_length else answer
    return text, choices


def sample_text(placeholder: str | None, label: str) -> str:
    """The placeholder without its "e.g.", or a stand-in when the question has no placeholder."""
    if not placeholder:
        return f"Test answer for: {label}"
    for prefix in EXAMPLE_PREFIXES:
        if placeholder.lower().startswith(prefix):
            return placeholder[len(prefix) :].strip()
    return placeholder.strip()
