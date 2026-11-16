"""Exercise the isolated Teams directory-search preparation branch."""

from pathlib import Path

from test_teams_web_dom import page  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "src/office_bridge/native/teams/lookup.js").read_text(encoding="utf-8")


def install_search_popup(page, query, candidates):  # noqa: F811
    page.evaluate(
        """([query,candidates]) => {
          const input=document.createElement('input');
          input.id='ms-searchux-input';
          input.setAttribute('data-tid','AUTOSUGGEST_INPUT');
          input.setAttribute('role','combobox');
          input.setAttribute('aria-controls','search-popup');
          input.value=query;
          document.body.append(input);
          const popup=document.createElement('div');
          popup.id='search-popup';
          popup.setAttribute('data-tid','ms-searchux-popup');
          popup.setAttribute('role','listbox');
          const group=document.createElement('div');
          group.setAttribute('role','group');
          group.setAttribute('data-tid','AUTOSUGGEST_GROUP_TOPHITS');
          popup.append(group);
          for(const [id,aria,title] of candidates) {
            const option=document.createElement('div');
            option.id=id;
            option.setAttribute('role','option');
            option.setAttribute('data-tid','AUTOSUGGEST_SUGGESTION_TOPHITS');
            option.setAttribute('aria-label',aria);
            const label=document.createElement('span');
            label.setAttribute('data-tid','AUTOSUGGEST_SUGGESTION_TITLE');
            label.textContent=title;
            option.append(label);
            group.append(option);
          }
          document.body.append(popup);
        }""",
        [query, candidates],
    )


def search_results(page, query, limit):  # noqa: F811
    result = None
    for _ in range(8):
        result = page.evaluate(
            "([source,request]) => (0,eval)('('+source+')')(request)",
            [SCRIPT, {"phase": "search_results", "query": query, "limit": limit}],
        )
        if result["state"] != "waiting":
            break
        page.wait_for_timeout(250)
    return result


def test_prepare_search_focuses_and_selects_existing_query_without_sending(page):  # noqa: F811
    page.locator("body").evaluate(
        "body => { const input=document.createElement('input'); "
        "input.id='ms-searchux-input'; input.setAttribute('data-tid','AUTOSUGGEST_INPUT'); "
        "input.setAttribute('role','combobox'); input.value='existing query'; "
        "document.body.append(input); }"
    )
    result = page.evaluate(
        "([source,request]) => (0,eval)('('+source+')')(request)",
        [SCRIPT, {"phase": "prepare_search"}],
    )
    assert result["state"] == "search_prepared"
    assert result["input"] == {
        "id": "ms-searchux-input",
        "tid": "AUTOSUGGEST_INPUT",
        "role": "combobox",
    }
    assert page.evaluate(
        "() => { const input=document.getElementById('ms-searchux-input'); "
        "return document.activeElement === input && input.value === 'existing query' && "
        "input.selectionStart === 0 && input.selectionEnd === input.value.length; }"
    )
    assert page.evaluate("window.clicks || 0") == 0


def test_prepare_search_preserves_existing_composer_draft(page):  # noqa: F811
    page.locator('[data-tid="ckeditor"]').fill("unsent draft")
    page.locator("body").evaluate(
        "body => { const input=document.createElement('input'); "
        "input.id='ms-searchux-input'; input.setAttribute('data-tid','AUTOSUGGEST_INPUT'); "
        "input.setAttribute('role','combobox'); input.value='existing query'; "
        "input.setSelectionRange(2,4); document.body.append(input); }"
    )
    page.locator('[data-tid="ckeditor"]').focus()
    result = page.evaluate(
        "([source,request]) => (0,eval)('('+source+')')(request)",
        [SCRIPT, {"phase": "prepare_search"}],
    )
    assert result["state"] == "draft_exists"
    assert page.evaluate(
        "() => { const input=document.getElementById('ms-searchux-input'); "
        "const editor=document.querySelector('[data-tid=ckeditor]'); "
        "return document.activeElement===editor && input.value==='existing query' && "
        "input.selectionStart===2 && input.selectionEnd===4 && editor.innerText==='unsent draft'; }"
    )


def test_diagnostics_reports_composer_state_without_content(page):  # noqa: F811
    def diagnostics():
        return page.evaluate(
            "([source,request]) => (0,eval)('('+source+')')(request)",
            [SCRIPT, {"phase": "diagnostics"}],
        )

    clean = diagnostics()
    assert clean["composer"] == {"editor_count": 1, "dirty": False}

    page.locator('[data-tid="ckeditor"]').fill("private unsent draft")
    dirty = diagnostics()
    assert dirty["composer"] == {"editor_count": 1, "dirty": True}
    assert "private unsent draft" not in str(dirty)


def test_open_copilot_uses_unique_appbar_control_when_composer_is_clean(page):  # noqa: F811
    page.locator('[data-tid="app-bar-wrapper"]').evaluate(
        "root => { const button=document.createElement('button'); "
        "button.setAttribute('aria-label','Copilot'); "
        "button.onclick=()=>window.copilotClicks=(window.copilotClicks||0)+1; "
        "root.append(button); }"
    )
    opened = page.evaluate(
        "([source,request]) => (0,eval)('('+source+')')(request)",
        [SCRIPT, {"phase": "open_copilot"}],
    )
    assert opened == {
        "state": "copilot_open_clicked",
        "composer": {"editor_count": 1, "dirty": False},
    }
    assert page.evaluate("window.copilotClicks") == 1


def test_open_copilot_stops_when_composer_has_a_draft(page):  # noqa: F811
    page.locator('[data-tid="app-bar-wrapper"]').evaluate(
        "root => { const button=document.createElement('button'); "
        "button.setAttribute('aria-label','Copilot'); "
        "button.onclick=()=>window.copilotClicks=(window.copilotClicks||0)+1; "
        "root.append(button); }"
    )
    page.locator('[data-tid="ckeditor"]').fill("keep this unsent draft")
    blocked = page.evaluate(
        "([source,request]) => (0,eval)('('+source+')')(request)",
        [SCRIPT, {"phase": "open_copilot"}],
    )
    assert blocked == {
        "state": "draft_exists",
        "composer": {"editor_count": 1, "dirty": True},
    }
    assert page.evaluate("window.copilotClicks || 0") == 0
    assert page.locator('[data-tid="ckeditor"]').inner_text() == "keep this unsent draft"


def test_unique_person_survives_more_nonmatching_groups_than_public_limit(page):  # noqa: F811
    candidates = [
        [
            "person-example",
            "Person Person, Example (person@example.invalid)",
            "Person, Example",
        ]
    ]
    candidates.extend(
        [
            [f"group-{index}", f"Group chat Unrelated {index}", f"Unrelated {index}"]
            for index in range(6)
        ]
    )
    install_search_popup(page, "Person, Example", candidates)
    result = search_results(page, "Person, Example", 5)
    assert result["state"] == "search_results"
    assert result["truncated"] is False
    assert len(result["candidates"]) == 7
    assert len(result["candidates"]) <= 100
    assert [
        candidate["title"]
        for candidate in result["candidates"]
        if candidate["type"] == "person" and candidate["exact_match"]
    ] == ["Person, Example"]


def test_multiple_exact_people_remain_ambiguous(page):  # noqa: F811
    candidates = [
        ["person-one", "Person Example Person", "Example Person"],
        ["person-two", "Person Example Person", "Example Person"],
        *[
            [f"group-{index}", f"Group chat Unrelated {index}", f"Unrelated {index}"]
            for index in range(6)
        ],
    ]
    install_search_popup(page, "Example Person", candidates)
    result = search_results(page, "Example Person", 5)
    assert result["state"] == "ambiguous"
    assert result["truncated"] is False
    assert (
        sum(
            candidate["type"] == "person" and candidate["exact_match"]
            for candidate in result["candidates"]
        )
        == 2
    )


def test_search_fails_closed_after_internal_candidate_scan_bound(page):  # noqa: F811
    candidates = [["person-example", "Person Person, Example", "Person, Example"]]
    candidates.extend(
        [
            [f"group-{index}", f"Group chat Unrelated {index}", f"Unrelated {index}"]
            for index in range(100)
        ]
    )
    install_search_popup(page, "Person, Example", candidates)
    result = search_results(page, "Person, Example", 5)
    assert result["state"] == "ambiguous"
    assert result["truncated"] is True
    assert len(result["candidates"]) == 100


def test_resolved_chat_waits_for_header_to_match_selected_person(page):  # noqa: F811
    page.evaluate(
        """() => window.__officeLookupSelection = {
          query:'person, example', key:'person-example', type:'person',
          title:'Person, Example', selected:true, lastHeader:null, stableCount:0
        }"""
    )
    request = {
        "phase": "resolved_chat",
        "query": "Person, Example",
        "key": "person-example",
        "type": "person",
        "title": "Person, Example",
    }
    stale = page.evaluate(
        "([source,request]) => (0,eval)('('+source+')')(request)", [SCRIPT, request]
    )
    assert stale["state"] == "waiting"
    chat_id = "19:" + "c" * 32 + "@thread.v2"
    page.evaluate(
        """([chatId,title]) => {
          document.querySelector('[id^=chat-header-]').id='chat-header-'+chatId;
          document.querySelector('[data-tid=chat-title]').textContent=title;
        }""",
        [chat_id, "Person, Example"],
    )
    first = page.evaluate(
        "([source,request]) => (0,eval)('('+source+')')(request)", [SCRIPT, request]
    )
    second = page.evaluate(
        "([source,request]) => (0,eval)('('+source+')')(request)", [SCRIPT, request]
    )
    assert first["state"] == "waiting"
    assert second == {
        "state": "chat_resolved",
        "chat": {"chat_id": chat_id, "title": "Person, Example"},
    }
