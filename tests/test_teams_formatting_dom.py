import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from office_bridge.teams.formatting import format_body

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "src/office_bridge/native/teams/formatting.js").read_text(encoding="utf-8")


@pytest.fixture
def page():
    executable = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "robocorp/playwright/chromium-1254/chrome-win64/chrome.exe"
    )
    if not executable.is_file():
        pytest.skip("isolated Chromium test browser is not installed")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(executable), headless=True)
        page = browser.new_page()
        page.set_content(
            """<!doctype html><html><head>
            <meta http-equiv="Content-Security-Policy"
                  content="require-trusted-types-for 'script'">
            </head><body><div id="root"></div></body></html>"""
        )
        yield page
        browser.close()


def render(page, nodes):
    return page.evaluate(
        """([source, nodes]) => {
          const renderNodes = (0, eval)('(' + source + ')');
          const template = renderNodes(nodes);
          if (!template) return null;
          return {
            html: template.innerHTML,
            text: template.content.textContent,
            bold: template.content.querySelector('strong')?.textContent || null,
            link: template.content.querySelector('a')?.getAttribute('href') || null,
            script: !!template.content.querySelector('script')
          };
        }""",
        [SCRIPT, nodes],
    )


def test_dom_builder_works_when_trusted_types_rejects_html_assignment(page):
    assignment_failed = page.evaluate(
        """() => {
          try {
            document.createElement('template').innerHTML = '<strong>blocked</strong>';
            return false;
          } catch (error) {
            return error instanceof TypeError;
          }
        }"""
    )
    assert assignment_failed is True

    prepared = format_body("Intro **bold** and [guide](https://example.test/docs).", "markdown")
    rendered = render(page, prepared.nodes)
    assert rendered["bold"] == "bold"
    assert rendered["link"] == "https://example.test/docs"
    assert rendered["text"] == prepared.body
    assert "<strong>bold</strong>" in rendered["html"]


@pytest.mark.parametrize(
    "nodes",
    [
        [{"tag": "img", "attrs": {"src": "https://example.test/x"}, "children": []}],
        [{"tag": "p", "attrs": {"onclick": "alert(1)"}, "children": []}],
        [{"tag": "a", "attrs": {"href": "javascript:alert(1)"}, "children": []}],
        [{"tag": "a", "attrs": {"href": "https://user@example.test/"}, "children": []}],
        [{"tag": "a", "attrs": {"href": "https://@example.test/"}, "children": []}],
        [{"tag": "p", "attrs": {}, "children": [], "extra": True}],
    ],
)
def test_dom_builder_rejects_unsafe_or_malformed_nodes(page, nodes):
    assert render(page, nodes) is None


def test_escaped_raw_html_stays_literal_text(page):
    prepared = format_body("<script>alert(1)</script>", "markdown")
    rendered = render(page, prepared.nodes)
    assert rendered["script"] is False
    assert rendered["text"] == "<script>alert(1)</script>"
    assert "&lt;script&gt;" in rendered["html"]


def test_dom_builder_enforces_node_and_depth_bounds(page):
    too_many = [{"text": "x"} for _ in range(2049)]
    assert render(page, too_many) is None

    nested = {"text": "x"}
    for _ in range(16):
        nested = {"tag": "strong", "attrs": {}, "children": [nested]}
    assert render(page, [nested]) is None
