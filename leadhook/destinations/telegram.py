"""Telegram messages written in Markdown, sent as Telegram HTML."""

import html
import re

TEXT_LIMIT = 4096
# Placeholder name -> (path in the standard payload, label); any other name is an answer key.
LEAD_PLACEHOLDERS = {
    "form": ("form.name", "Nome del modulo"),
    "page": ("page.name", "Pagina"),
    "campaign": ("campaign.name", "Campagna"),
    "adset": ("adset.name", "Gruppo di inserzioni"),
    "ad": ("ad.name", "Inserzione"),
    "lead_id": ("id", "ID del lead"),
    "created_time": ("created_time", "Data di creazione"),
}
PLACEHOLDER = re.compile(r"\{\{?\s*([^{}\n]+?)\s*\}?\}")
TOKEN = re.compile(r"\x00(\d+)\x00")
INLINE = [
    (re.compile(r"`([^`\n]+)`"), r"<code>\1</code>"),
    (re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)"), r'<a href="\2">\1</a>'),
    (re.compile(r"\*\*(.+?)\*\*"), r"<b>\1</b>"),
    (re.compile(r"~~(.+?)~~"), r"<s>\1</s>"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"<i>\1</i>"),
    (re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])"), r"<i>\1</i>"),
]


def placeholder_value(name: str, context: dict) -> str:
    if name in context["data"]:
        value = context["data"][name]
    elif name in LEAD_PLACEHOLDERS:
        value = context
        for part in LEAD_PLACEHOLDERS[name][0].split("."):
            value = value.get(part, "") if isinstance(value, dict) else ""
    else:
        value = ""
    return ", ".join(map(str, value)) if isinstance(value, list) else str(value)


def render_markdown(template: str, context: dict) -> str:
    """Telegram HTML from Markdown with {placeholders}; values are inserted as plain text."""
    values: list[str] = []

    def hold(match: re.Match) -> str:
        # Values go in after the Markdown pass, so their own * or _ never format anything.
        values.append(html.escape(placeholder_value(match.group(1), context)))
        return f"\x00{len(values) - 1}\x00"

    lines = []
    for line in html.escape(PLACEHOLDER.sub(hold, template)).splitlines():
        heading = re.match(r"#{1,6}\s+(.*)", line)
        if heading:
            line = f"**{heading.group(1)}**"
        line = re.sub(r"^\s*[-*]\s+", "• ", line)
        for pattern, replacement in INLINE:
            line = pattern.sub(replacement, line)
        lines.append(line)
    text = TOKEN.sub(lambda m: values[int(m.group(1))], "\n".join(lines))
    # shortcut: cutting at the limit can split a tag; Telegram then rejects the message.
    return text[:TEXT_LIMIT]
