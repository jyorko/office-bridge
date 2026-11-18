from office_bridge.mail_body import readable_plain_mail, safe_mail_html
from office_bridge.presentation import Card, View, present


def test_mail_preserves_tables_and_discards_empty_word_paragraphs():
    html = safe_mail_html(
        '<p style="margin-bottom:80px">Changes</p><p><span>&nbsp;</span></p>'
        '<table width="100%"><tr><th>Question</th><th>Change</th></tr>'
        '<tr><td colspan="2"><strong>2.2</strong><br>Revised response</td></tr></table>'
    )
    assert "<p>Changes</p><table>" in html
    assert '<td colspan="2"><strong>2.2</strong><br>Revised response</td>' in html
    assert "style" not in html
    assert safe_mail_html(html) == html


def test_mail_never_imports_scripts_remote_resources_or_interactive_controls():
    html = safe_mail_html(
        "<script>alert(1)</script><style>body{display:none}</style>"
        '<img src="https://private.invalid/tracker"><a href="javascript:alert(1)">Read</a>'
        "<svg><script>alert(2)</script></svg><form><input><button>Send</button></form>"
        '<p onclick="alert(3)" id="action-status">Safe &amp; readable</p>'
        '<table><tr><td colspan="9999999999999999999" style="color:red">Cell</td></tr></table>'
    )
    assert html == "Read<p>Safe &amp; readable</p><table><tr><td>Cell</td></tr></table>"
    assert safe_mail_html("<p>" + "x" * 200_001) == ""


def test_plain_mail_spacing_is_display_only_and_rich_markup_reaches_renderer():
    body = "First paragraph\n \n\n\n\nSecond paragraph"
    card = Card(
        kind="mail",
        heading="Mail",
        body=body,
        body_html="<p>First paragraph</p><p>Second paragraph</p>",
    )
    display = present(View(source="Outlook", cards=[card]))
    assert card.body == body
    assert 'class="body mail-body"' in display.html
    assert readable_plain_mail(body) == "First paragraph\n\nSecond paragraph"
