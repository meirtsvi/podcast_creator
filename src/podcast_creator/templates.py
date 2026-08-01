"""Jinja2 rendering for the prompt templates.

Replaces the previous scheme of `str.replace("{name}", ...)` calls plus the
`<!--CONDITIONAL:X-->` / `<!--END:X-->` markers, which were expanded by a
`process_conditional_text` helper that this module made redundant.

Templates live next to this module as `.j2` files. `StrictUndefined` is used on
purpose: a mistyped or forgotten variable raises at render time, which happens
before any paid model call, instead of silently baking an empty string into the
prompt.
"""

from pathlib import Path as p

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from podcast_creator.logger import logger

TEMPLATE_DIR = p(__file__).parent

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def render_template(template_name: str, **context) -> str:
    """Render `template_name` with `context` and collapse the blank-line runs that
    stripped-out conditional blocks leave behind."""
    rendered = _env.get_template(template_name).render(**context)

    # The old process_conditional_text() did this after dropping a block; keep the
    # behaviour so prompts do not gain long gaps where a conditional was removed.
    lines = rendered.split("\n")
    collapsed, blanks = [], 0
    for line in lines:
        if line.strip():
            blanks = 0
            collapsed.append(line)
        else:
            blanks += 1
            if blanks <= 2:
                collapsed.append(line)
    result = "\n".join(collapsed)

    logger.info(f"Rendered template {template_name} ({len(result)} chars)")
    return result


def render_string(template_source: str, **context) -> str:
    """Render a template held in memory rather than on disk."""
    return _env.from_string(template_source).render(**context)
