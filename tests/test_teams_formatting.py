import json

import pytest

from office_bridge.teams.formatting import BodyFormatError, format_body
from office_bridge.teams.service import _prepared_arguments, _rich_identity


def test_markdown_produces_visible_text_html_and_native_format_requirements():
    prepared = format_body(
        "Intro **bold** and *italic* with `code` and [guide](https://example.test/docs).\n\n- first\n- second",
        "markdown",
    )
    assert prepared.body == "Intro bold and italic with code and guide.\n\nfirst\nsecond"
    assert prepared.html is not None
    assert "<strong>bold</strong>" in prepared.html
    assert "<em>italic</em>" in prepared.html
    assert '<a href="https://example.test/docs">guide</a>' in prepared.html
    assert prepared.formatting == {
        "bold": ["bold"],
        "italic": ["italic"],
        "code": ["code"],
        "links": [{"text": "guide", "href": "https://example.test/docs"}],
        "unordered_list": True,
        "ordered_list": False,
    }
    assert prepared.nodes == [
        {
            "tag": "p",
            "attrs": {},
            "children": [
                {"text": "Intro "},
                {"tag": "strong", "attrs": {}, "children": [{"text": "bold"}]},
                {"text": " and "},
                {"tag": "em", "attrs": {}, "children": [{"text": "italic"}]},
                {"text": " with "},
                {"tag": "code", "attrs": {}, "children": [{"text": "code"}]},
                {"text": " and "},
                {
                    "tag": "a",
                    "attrs": {"href": "https://example.test/docs"},
                    "children": [{"text": "guide"}],
                },
                {"text": "."},
            ],
        },
        {
            "tag": "ul",
            "attrs": {},
            "children": [
                {"tag": "li", "attrs": {}, "children": [{"text": "first"}]},
                {"tag": "li", "attrs": {}, "children": [{"text": "second"}]},
            ],
        },
    ]


def test_markdown_keeps_plain_mode_backwards_compatible():
    body = "**this stays literal**\nno conversion"
    prepared = format_body(body)
    assert prepared.body == body
    assert prepared.html is None
    assert prepared.formatting == {
        "bold": [],
        "italic": [],
        "code": [],
        "links": [],
        "unordered_list": False,
        "ordered_list": False,
    }
    assert prepared.nodes is None


def test_ordered_lists_are_projected_and_marked_for_native_verification():
    prepared = format_body("1. first\n2. second", "markdown")
    assert prepared.body == "first\nsecond"
    assert prepared.formatting["ordered_list"] is True
    assert prepared.formatting["unordered_list"] is False


def test_raw_html_is_escaped_and_not_emitted_as_markup():
    prepared = format_body("<script>alert(1)</script>", "markdown")
    assert prepared.html is not None
    assert "<script>" not in prepared.html
    assert "&lt;script&gt;" in prepared.html


def test_images_are_rejected_and_insecure_links_are_not_created():
    with pytest.raises(BodyFormatError, match="images"):
        format_body("![pixel](https://example.test/pixel.png)", "markdown")
    unsafe = format_body("[open](javascript:alert(1))", "markdown")
    assert "<a " not in unsafe.html


def test_markdown_body_and_rendered_html_are_bounded():
    with pytest.raises(BodyFormatError, match="2000"):
        format_body("x" * 2001, "markdown")
    escaped = format_body("<iframe src='https://example.test'></iframe>", "markdown")
    assert "<iframe" not in escaped.html
    assert "&lt;iframe" in escaped.html


def test_prepared_nodes_are_forwarded_and_part_of_rich_identity():
    prepared = format_body("**bold**", "markdown")
    arguments = _prepared_arguments(prepared, "markdown")
    identity = _rich_identity("**bold**", "markdown", prepared)
    assert arguments["nodes"] == prepared.nodes
    assert json.loads(identity)["nodes"] == prepared.nodes
    plain = format_body("**literal**")
    assert _prepared_arguments(plain, "plain")["nodes"] is None
    assert _rich_identity("**literal**", "plain", plain) == "**literal**"
