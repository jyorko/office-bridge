from pathlib import Path

import pytest
from test_teams_web_dom import page  # noqa: F401

SCRIPT = (
    Path(__file__).resolve().parents[1] / "src/office_bridge/native/teams/copilot.js"
).read_text(encoding="utf-8")


@pytest.mark.parametrize("empty_reply_placeholder", [False, True])
def test_copilot_guards_drafts_work_iq_and_duplicate_clicks(page, empty_reply_placeholder):  # noqa: F811
    html = """<button role="switch" aria-label="Turn off Work IQ" aria-checked="true">Work IQ</button>
      <span style="display:inline-block;min-width:100px;min-height:30px" id="m365-chat-editor-target-element" role="textbox" aria-label="Message Copilot" contenteditable="true" data-lexical-editor="true"></span>
      <button aria-label="Send" onclick="window.clicks=(window.clicks||0)+1">Send</button>"""
    page.route(
        "https://m365copilotapp.svc.cloud.microsoft/**",
        lambda route: route.fulfill(body=html, content_type="text/html"),
    )
    page.goto("https://m365copilotapp.svc.cloud.microsoft/chat")

    def run(phase):
        return page.evaluate(
            "([source,request])=>(0,eval)('('+source+')')(request)",
            [SCRIPT, {"phase": phase, "question": "Find project decisions", "requestId": "test-1"}],
        )

    page.locator('[role="switch"]').evaluate("e=>e.setAttribute('aria-checked','false')")
    assert run("prepare")["state"] == "not_ready"
    page.locator('[role="switch"]').evaluate("e=>e.setAttribute('aria-checked','true')")
    editor = page.locator('[role="textbox"]')
    editor.fill("Existing draft")
    assert run("prepare")["state"] == "draft_exists"
    assert editor.inner_text() == "Existing draft"
    editor.fill("")
    assert run("prepare")["state"] == "prepared"
    editor.evaluate("""e=>{const marker=document.createElement('span');
      marker.setAttribute('aria-hidden','true');marker.setAttribute('data-lexical-text','true');
      marker.textContent='\\u200b\\u200c';e.append(marker)}""")
    prepared = run("read")
    assert prepared["state"] == "not_submitted" and prepared["question_matches"] is True
    assert run("submit")["state"] == "clicked"
    assert run("status")["state"] == "not_ready"
    assert run("submit")["state"] == "already_clicked"
    assert page.evaluate("window.clicks") == 1
    assert run("read")["state"] == "awaiting_response"
    page.evaluate("""()=>{
      const turn=document.createElement('div');
      turn.setAttribute('data-testid','m365-chat-llm-web-ui-chat-message');
      turn.innerHTML='<p>Find project decisions</p><div data-testid="copilot-message-div" id="chatMessageResponse-example1">'
        +'<div data-testid="markdown-reply"><p>Observed project decision.</p>'
        +'<a href="https://example.invalid/source">Evidence</a>'
        +'<a href="javascript:alert(1)">Unsafe link</a></div></div>'
        +'<button data-testid="CopyButtonTestId" aria-label="Copy Response">Copy</button>';
      document.body.append(turn);
      window.testNow=10000;Date.now=()=>window.testNow;
    }""")
    if empty_reply_placeholder:
        page.locator('[data-testid="copilot-message-div"]').evaluate("""e=>{
          const placeholder=document.createElement('div');
          placeholder.setAttribute('data-testid','markdown-reply');
          placeholder.style.cssText='min-width:20px;min-height:20px';
          placeholder.textContent='\\n ';
          e.prepend(placeholder);
        }""")
        assert page.locator('[data-testid="markdown-reply"]').first.is_visible()
    page.locator('[data-testid="m365-chat-llm-web-ui-chat-message"] > p').evaluate(
        "e=>e.textContent='Unrelated question'"
    )
    assert run("read")["submission_verified"] is False
    page.locator('[data-testid="m365-chat-llm-web-ui-chat-message"] > p').evaluate(
        "e=>e.textContent='Find project decisions'"
    )
    waiting = run("read")
    assert waiting["state"] == "awaiting_response" and waiting["submission_verified"] is True
    page.evaluate("window.testNow+=2500")
    page.evaluate("""()=>{const button=document.createElement('button');button.id='stop-test';
      button.setAttribute('aria-label','Stop generating');document.body.append(button)}""")
    assert run("read")["state"] == "awaiting_response"
    page.locator("#stop-test").evaluate("e=>e.remove()")
    page.locator('[data-testid="m365-chat-llm-web-ui-chat-message"]').evaluate(
        "e=>e.setAttribute('aria-busy','true')"
    )
    assert run("read")["state"] == "awaiting_response"
    page.locator('[data-testid="m365-chat-llm-web-ui-chat-message"]').evaluate(
        "e=>e.removeAttribute('aria-busy')"
    )
    completed = run("read")
    assert completed["state"] == "completed" and completed["submission_verified"] is True
    assert "Observed project decision." in completed["answer"]
    assert completed["citations"] == [
        {"title": "Evidence", "url": "https://example.invalid/source"}
    ]
    assert completed["citations_complete"] is False
    assert page.evaluate("window.clicks") == 1


def test_delayed_editor_update_is_verified_without_reinserting_or_sending(page):  # noqa: F811
    html = """<button role="switch" aria-label="Turn off Work IQ" aria-checked="true">Work IQ</button>
      <span style="display:inline-block;min-width:100px;min-height:30px" id="m365-chat-editor-target-element" role="textbox" aria-label="Message Copilot" contenteditable="true" data-lexical-editor="true"></span>
      <button aria-label="Send" onclick="window.clicks=(window.clicks||0)+1">Send</button>"""
    page.route(
        "https://m365copilotapp.svc.cloud.microsoft/**",
        lambda route: route.fulfill(body=html, content_type="text/html"),
    )
    page.goto("https://m365copilotapp.svc.cloud.microsoft/chat")
    page.evaluate("""()=>{document.execCommand=()=>{
      window.insertions=(window.insertions||0)+1;return true;
    }}""")

    def run(phase):
        return page.evaluate(
            "([source,request])=>(0,eval)('('+source+')')(request)",
            [SCRIPT, {"phase": phase, "question": "Find decisions", "requestId": "delayed-1"}],
        )

    assert run("prepare")["state"] == "input_unverified"
    assert run("verify_prepare")["state"] == "input_unverified"
    page.locator('[role="textbox"]').fill("Find decisions")
    assert run("verify_prepare")["state"] == "prepared"
    assert page.evaluate("window.insertions") == 1
    assert page.evaluate("window.clicks||0") == 0
    page.locator('[role="textbox"]').fill("Changed draft")
    assert run("verify_prepare")["state"] == "input_unverified"
    assert run("submit")["state"] == "draft_changed"
    assert page.evaluate("window.clicks||0") == 0


def test_model_dropdown_is_discovered_and_changes_only_an_enabled_choice(page):  # noqa: F811
    html = """<button role="switch" aria-label="Turn off Work IQ" aria-checked="true">Work IQ</button>
      <span style="display:inline-block;min-width:100px;min-height:30px" id="m365-chat-editor-target-element" role="textbox" aria-label="Message Copilot" contenteditable="true" data-lexical-editor="true"></span>
      <button id="model-button" aria-label="Example, Model Selector" aria-controls="models" onclick="document.getElementById('models').hidden=!document.getElementById('models').hidden">Models</button>
      <div id="models" role="menu" aria-labelledby="model-button" hidden>
        <button role="menuitemradio" aria-checked="true">Example</button>
        <button role="menuitemradio" aria-checked="false" onclick="document.getElementById('model-button').setAttribute('aria-label','Alternative, Model Selector');document.getElementById('models').hidden=true">Alternative</button>
        <button role="menuitemradio" aria-disabled="true">Unavailable</button>
      </div>"""
    page.route(
        "https://m365copilotapp.svc.cloud.microsoft/**",
        lambda route: route.fulfill(body=html, content_type="text/html"),
    )
    page.goto("https://m365copilotapp.svc.cloud.microsoft/chat")

    def run(phase, model=None):
        return page.evaluate(
            "([source,request])=>(0,eval)('('+source+')')(request)",
            [SCRIPT, {"phase": phase, "model": model}],
        )

    assert run("models")["state"] == "model_menu_opening"
    choices = run("models")
    assert choices["model"] == "Example" and len(choices["models"]) == 3
    assert choices["models"][-1]["enabled"] is False
    assert page.locator("#models").is_hidden()
    assert run("select_model", "Alternative")["state"] == "model_menu_opening"
    assert run("select_model", "Alternative")["state"] == "model_selection_pending"
    assert run("select_model", "Alternative") == {"state": "model_selected", "model": "Alternative"}
    assert run("status")["model"] == "Alternative"
    page.locator('[role="textbox"]').fill("Existing draft")
    assert run("select_model", "Example")["state"] == "draft_exists"
    assert page.locator('[role="textbox"]').inner_text() == "Existing draft"


def test_model_submenus_are_discovered_without_selecting_and_exact_model_is_verified(page):  # noqa: F811
    html = """<button role="switch" aria-label="Turn off Work IQ" aria-checked="true">Work IQ</button>
      <span style="display:inline-block;min-width:100px;min-height:30px" id="m365-chat-editor-target-element" role="textbox" aria-label="Message Copilot" contenteditable="true" data-lexical-editor="true"></span>
      <button id="model-button" aria-label="Think deeper, Model Selector" aria-controls="models"
        onclick="models.hidden=!models.hidden;claude.hidden=true;gpt.hidden=true">Models</button>
      <div id="models" role="menu" aria-labelledby="model-button" hidden>
        <button role="menuitemradio">Auto</button>
        <button role="menuitemradio" aria-checked="true">Think deeper</button>
        <button id="claude-trigger" role="menuitem" aria-haspopup="menu" aria-controls="claude"
          onclick="claude.hidden=false;gpt.hidden=true">Claude<br>Anthropic</button>
        <button id="gpt-trigger" role="menuitem" aria-haspopup="menu" aria-controls="gpt"
          onclick="gpt.hidden=false;claude.hidden=true">GPT<br>OpenAI</button>
      </div>
      <div id="claude" role="menu" aria-labelledby="claude-trigger" hidden>
        <button role="menuitemradio">Sonnet 5.5</button>
        <button role="menuitemradio" onclick="window.selections=(window.selections||0)+1;
          document.getElementById('model-button').setAttribute('aria-label','Opus 5.5, Model Selector');
          models.querySelector('[aria-checked]').setAttribute('aria-checked','false');
          models.hidden=true;claude.hidden=true">Opus 5.5</button>
        <button role="menuitemradio" aria-disabled="true">Unavailable</button>
      </div>
      <div id="gpt" role="menu" aria-labelledby="gpt-trigger" hidden>
        <button role="menuitemradio">GPT example</button>
      </div>"""
    page.route(
        "https://m365copilotapp.svc.cloud.microsoft/**",
        lambda route: route.fulfill(body=html, content_type="text/html"),
    )
    page.goto("https://m365copilotapp.svc.cloud.microsoft/chat")

    def run(phase, model=None):
        return page.evaluate(
            "([source,request])=>(0,eval)('('+source+')')(request)",
            [SCRIPT, {"phase": phase, "model": model}],
        )

    def finish(phase, model=None):
        for _ in range(8):
            result = run(phase, model)
            if result["state"] not in {"model_menu_opening", "model_selection_pending"}:
                return result
        raise AssertionError("Model traversal did not finish")

    choices = finish("models")
    assert choices["state"] == "models"
    assert [item["name"] for item in choices["models"]] == [
        "Auto",
        "Think deeper",
        "Sonnet 5.5",
        "Opus 5.5",
        "Unavailable",
        "GPT example",
    ]
    assert choices["model"] == "Think deeper"
    assert choices["models"][4]["enabled"] is False
    assert page.evaluate("window.selections||0") == 0
    assert page.locator("#models").is_hidden()
    assert finish("select_model", "Unavailable")["state"] == "model_unavailable"
    assert page.evaluate("window.selections||0") == 0
    assert finish("select_model", "Opus 5.5") == {"state": "model_selected", "model": "Opus 5.5"}
    assert page.evaluate("window.selections") == 1
    selected = finish("models")
    assert [item["name"] for item in selected["models"] if item["selected"]] == ["Opus 5.5"]
    page.locator('[role="textbox"]').fill("Existing draft")
    assert run("select_model", "Think deeper")["state"] == "draft_exists"
    assert page.locator('[role="textbox"]').inner_text() == "Existing draft"
