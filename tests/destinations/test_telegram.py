from leadhook.destinations.telegram import render_markdown

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
