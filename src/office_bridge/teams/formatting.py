"""Prepare bounded plain or CommonMark Teams message bodies."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

MAX_BODY_LENGTH = 2000
MAX_HTML_BYTES = 12_000
MAX_RICH_NODES = 2048
MAX_RICH_DEPTH = 16
_MENTION_TOKEN = re.compile(r"\{\{mention:([^{}\r\n]*)\}\}")
_MENTION_KEY = re.compile(r"^[a-z0-9-]{1,32}$")
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
_BLOCK_TAGS = {"blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "p", "pre"}
_ALLOWED_TAGS = _BLOCK_TAGS | {
    "a",
    "br",
    "code",
    "em",
    "hr",
    "li",
    "ol",
    "strong",
    "ul",
}


class BodyFormatError(ValueError):
    """A bounded Markdown or message-body validation error."""


@dataclass(frozen=True)
class PreparedBody:
    body: str
    html: str | None
    formatting: dict[str, object]
    nodes: list[dict[str, object]] | None


def _empty_formatting() -> dict[str, object]:
    return {
        "bold": [],
        "italic": [],
        "code": [],
        "links": [],
        "unordered_list": False,
        "ordered_list": False,
    }


def _check_link(href: str) -> None:
    try:
        parsed = urlsplit(href)
        valid = (
            parsed.scheme.lower() in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and (parsed.port is None or 1 <= parsed.port <= 65535)
            and not any(ord(char) < 32 or char.isspace() for char in href)
        )
    except ValueError:
        valid = False
    if not valid:
        raise BodyFormatError("Markdown links must use absolute HTTP or HTTPS URLs")


def _validate_tokens(tokens: list[Token]) -> None:
    for token in tokens:
        if token.type == "image":
            raise BodyFormatError("Markdown images are not supported")
        if token.type in {"blockquote_open", "heading_open", "hr"}:
            raise BodyFormatError("This Markdown block format is not supported")
        if token.type == "link_open":
            href = dict(token.attrs or []).get("href")
            if not isinstance(href, str):
                raise BodyFormatError("Markdown link destination is invalid")
            _check_link(href)
        if token.children:
            _validate_tokens(token.children)


class _Projection(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.captures: list[tuple[str, list[str]]] = []
        self.formatting = _empty_formatting()
        self.content_depth = 0
        self.nodes: list[dict[str, object]] = []
        self.node_stack: list[dict[str, object]] = []
        self.node_count = 0

    def _append_node(self, node: dict[str, object]) -> None:
        depth = len(self.node_stack) + 1
        self.node_count += 1
        if self.node_count > MAX_RICH_NODES or depth > MAX_RICH_DEPTH:
            raise BodyFormatError("Formatted message exceeds rich-text node limits")
        if self.node_stack:
            self.node_stack[-1]["children"].append(node)
        else:
            self.nodes.append(node)

    def _boundary(self, count: int = 1) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n" * count)

    def _text(self, value: str) -> None:
        self.parts.append(value)
        for _kind, captured in self.captures:
            captured.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_TAGS:
            raise BodyFormatError("Generated Markdown contains an unsupported HTML element")
        attr = dict(attrs)
        if len(attr) != len(attrs):
            raise BodyFormatError("Generated Markdown contains duplicate HTML attributes")
        if tag == "a":
            if set(attr) - {"href", "title"} or not isinstance(attr.get("href"), str):
                raise BodyFormatError("Generated Markdown link is invalid")
            _check_link(attr["href"])
        elif tag == "ol":
            if set(attr) - {"start"} or ("start" in attr and not (attr["start"] or "").isdigit()):
                raise BodyFormatError("Generated ordered list is invalid")
        elif attr:
            raise BodyFormatError("Generated Markdown contains an unsupported HTML attribute")

        node_attrs = {name: value for name, value in attrs if value is not None}
        node = {"tag": tag, "attrs": node_attrs, "children": []}
        self._append_node(node)
        if tag not in {"br", "hr"}:
            self.node_stack.append(node)

        if tag in _BLOCK_TAGS or tag in {"ul", "ol", "li"}:
            self._boundary(2 if tag in _BLOCK_TAGS else 1)
        if tag in _BLOCK_TAGS | {"li"}:
            self.content_depth += 1
        if tag == "ul":
            self.formatting["unordered_list"] = True
        elif tag == "ol":
            self.formatting["ordered_list"] = True
        elif tag in {"strong", "b"}:
            self.captures.append(("bold", []))
        elif tag in {"em", "i"}:
            self.captures.append(("italic", []))
        elif tag == "code":
            self.captures.append(("code", []))
        elif tag == "a":
            self.captures.append(("link:" + str(attr["href"]), []))
        elif tag == "br":
            self._text("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"br", "hr"}:
            return
        if not self.node_stack or self.node_stack[-1]["tag"] != tag:
            raise BodyFormatError("Generated Markdown contains unbalanced HTML")
        self.node_stack.pop()
        if tag in {"strong", "b", "em", "i", "code", "a"}:
            expected = {
                "strong": "bold",
                "b": "bold",
                "em": "italic",
                "i": "italic",
                "code": "code",
                "a": None,
            }[tag]
            index = next(
                (
                    i
                    for i in range(len(self.captures) - 1, -1, -1)
                    if self.captures[i][0].startswith(expected or "link:")
                ),
                None,
            )
            if index is None:
                raise BodyFormatError("Generated Markdown formatting is unbalanced")
            kind, captured = self.captures.pop(index)
            text = "".join(captured)
            if kind == "bold":
                self.formatting["bold"].append(text)
            elif kind == "italic":
                self.formatting["italic"].append(text)
            elif kind == "code":
                self.formatting["code"].append(text)
            else:
                self.formatting["links"].append({"text": text, "href": kind.removeprefix("link:")})
        if tag in _BLOCK_TAGS | {"li"}:
            self.content_depth = max(0, self.content_depth - 1)
        if tag in _BLOCK_TAGS | {"ul", "ol", "li"}:
            self._boundary(1 if tag == "li" else 2)

    def handle_data(self, data: str) -> None:
        if data.isspace() and self.content_depth == 0:
            return
        if data:
            self._append_node({"text": data})
        self._text(data)


def format_body(body: str, body_format: str = "plain") -> PreparedBody:
    """Return canonical visible text and bounded formatting data for the native editor."""
    if body_format not in {"plain", "markdown"}:
        raise BodyFormatError("Body format must be plain or markdown")
    if not isinstance(body, str) or not body or len(body) > MAX_BODY_LENGTH or "\x00" in body:
        raise BodyFormatError("Message body must contain 1 to 2000 non-NUL characters")
    if body_format == "plain":
        return PreparedBody(body=body, html=None, formatting=_empty_formatting(), nodes=None)

    tokens = _MARKDOWN.parse(body)
    _validate_tokens(tokens)
    rendered = _MARKDOWN.renderer.render(tokens, _MARKDOWN.options, {})
    if len(rendered.encode("utf-8")) > MAX_HTML_BYTES:
        raise BodyFormatError("Formatted message is too large")
    projection = _Projection()
    try:
        projection.feed(rendered)
        projection.close()
        if projection.node_stack:
            raise BodyFormatError("Generated Markdown contains unbalanced HTML")
    except BodyFormatError:
        raise
    except Exception:
        raise BodyFormatError("Could not safely parse formatted message") from None
    canonical = "".join(projection.parts).strip("\n")
    if not canonical or len(canonical) > MAX_BODY_LENGTH:
        raise BodyFormatError("Formatted message must contain 1 to 2000 visible characters")
    return PreparedBody(
        body=canonical,
        html=rendered,
        formatting=projection.formatting,
        nodes=projection.nodes,
    )


def validate_mention_placeholders(body: str, body_format: str, keys: set[str]) -> list[str]:
    """Validate mention placeholders in plain text or permitted Markdown text nodes."""
    if body_format == "plain":
        occurrences = _MENTION_TOKEN.findall(body)
        if any(not _MENTION_KEY.fullmatch(key) for key in occurrences):
            raise BodyFormatError("Mention placeholders must use a lowercase key.")
        if any(key not in keys for key in occurrences):
            raise BodyFormatError("Every mention placeholder must match its plan.")
        if set(occurrences) != keys or len(occurrences) != len(keys):
            raise BodyFormatError("Each planned mention placeholder must appear exactly once.")
        return occurrences

    if body_format != "markdown":
        raise BodyFormatError("Body format must be plain or markdown")

    block_stack: list[str] = []
    occurrences: list[str] = []
    for token in _MARKDOWN.parse(body):
        if token.type == "inline":
            permitted_block = "paragraph_open" in block_stack or "list_item_open" in block_stack
            inline_stack: list[str] = []
            for child in token.children or []:
                if child.type in {"text", "code_inline", "html_inline"}:
                    matches = _MENTION_TOKEN.findall(child.content)
                    if matches:
                        if (
                            child.type != "text"
                            or not permitted_block
                            or any(
                                kind in inline_stack
                                for kind in {
                                    "em_open",
                                    "strong_open",
                                    "link_open",
                                }
                            )
                        ):
                            raise BodyFormatError(
                                "Mention placeholders are allowed only in ordinary paragraph or list text."
                            )
                        if any(not _MENTION_KEY.fullmatch(key) for key in matches):
                            raise BodyFormatError("Mention placeholders must use a lowercase key.")
                        occurrences.extend(matches)
                if child.type.endswith("_open"):
                    inline_stack.append(child.type)
                elif child.type.endswith("_close") and inline_stack:
                    expected = child.type.removesuffix("_close") + "_open"
                    if inline_stack[-1] == expected:
                        inline_stack.pop()
        if token.type.endswith("_open") and token.type not in {"inline"}:
            block_stack.append(token.type)
        elif token.type.endswith("_close") and block_stack:
            expected = token.type.removesuffix("_close") + "_open"
            if block_stack[-1] == expected:
                block_stack.pop()

    if any(key not in keys for key in occurrences):
        raise BodyFormatError("Every mention placeholder must match its plan.")
    if set(occurrences) != keys or len(occurrences) != len(keys):
        raise BodyFormatError("Each planned mention placeholder must appear exactly once.")
    return occurrences
