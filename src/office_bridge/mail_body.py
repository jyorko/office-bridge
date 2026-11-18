"""Keep email document structure without executable markup or remote resources."""

import re
from html import escape
from html.parser import HTMLParser

TAGS = frozenset(
    "p br strong b em i u s ul ol li blockquote table thead tbody tfoot tr td th "
    "pre code h1 h2 h3 h4 h5 h6 hr".split()
)
OMIT = frozenset("script style head title template iframe object svg math form button".split())
VOID = frozenset({"br", "hr"})
MAX_HTML = 200_000


class _MailHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.open_tags = []
        self.omitted = []

    def handle_starttag(self, tag, attrs):
        if self.omitted:
            if tag in OMIT:
                self.omitted.append(tag)
            return
        if tag in OMIT:
            self.omitted.append(tag)
            return
        if tag not in TAGS:
            return
        spans = ""
        if tag in {"td", "th"}:
            for key, value in attrs:
                if (
                    key in {"colspan", "rowspan"}
                    and value
                    and len(value) <= 2
                    and value.isascii()
                    and value.isdecimal()
                ):
                    if 1 <= int(value) <= 50:
                        spans += f' {key}="{int(value)}"'
        self.parts.append(f"<{tag}{spans}>")
        if tag not in VOID:
            self.open_tags.append(tag)

    def handle_endtag(self, tag):
        if self.omitted:
            if tag == self.omitted[-1]:
                self.omitted.pop()
            return
        if tag in self.open_tags:
            while self.open_tags:
                opened = self.open_tags.pop()
                self.parts.append(f"</{opened}>")
                if opened == tag:
                    break

    def handle_data(self, data):
        if not self.omitted:
            self.parts.append(escape(data))


def safe_mail_html(source: str) -> str:
    if not source or len(source) > MAX_HTML:
        return ""
    parser = _MailHTML()
    parser.feed(source)
    parser.close()
    result = "".join(parser.parts) + "".join(f"</{tag}>" for tag in reversed(parser.open_tags))
    result = re.sub(r"<p>(?:\s|&nbsp;|<br>)*</p>", "", result)
    return result.strip()


def readable_plain_mail(source: str) -> str:
    return re.sub(r"\n(?:[ \t\r]*\n){2,}", "\n\n", source).strip()
