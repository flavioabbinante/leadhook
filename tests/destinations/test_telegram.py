from leadhook.destinations.telegram import TEXT_LIMIT, render_markdown, truncate_html

CONTEXT = {
    "data": {
        "nome_e_cognome": "Mario *Rossi* <b>",
        "e-mail": "m_r@example.com",
        "tags": ["a", "b"],
    },
    "form": {"name": "Casa"},
    "campaign": {"name": "Autunno"},
}


def test_markdown_becomes_telegram_html_with_escaped_values():
    template = (
        "# Nuovo lead\n"
        "**Nome:** {nome_e_cognome}\n"
        "- _Email_: {{ e-mail }}\n"
        "~~old~~ `{form}` [sito](https://example.com/?a=1&b=2)\n"
        "{tags} {campaign} {missing} 5 < 6"
    )
    assert render_markdown(template, CONTEXT) == (
        "<b>Nuovo lead</b>\n"
        "<b>Nome:</b> Mario *Rossi* &lt;b&gt;\n"
        "• <i>Email</i>: m_r@example.com\n"
        '<s>old</s> <code>Casa</code> <a href="https://example.com/?a=1&amp;b=2">sito</a>\n'
        "a, b Autunno  5 &lt; 6"
    )


def test_long_message_is_cut_without_breaking_html():
    text = render_markdown("**{full_name}**", {"data": {"full_name": "x" * 5000}})
    assert len(text) <= TEXT_LIMIT
    assert text.startswith("<b>x") and text.endswith("x</b>")


def test_truncate_html_keeps_tags_and_entities_whole():
    text = '<b>a &amp; <a href="https://x.it/?a=1&amp;b=2">link</a></b>'
    for limit in range(len(text)):
        cut = truncate_html(text, limit)
        assert len(cut) <= limit
        assert cut.count("<b>") == cut.count("</b>")
        assert cut.count("<a ") == cut.count("</a>")
        assert "&" not in cut.replace("&amp;", "")
    assert truncate_html(text, 18) == "<b>a &amp; </b>"
