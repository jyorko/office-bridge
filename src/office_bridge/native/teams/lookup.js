(request) => {
  const {phase, query, limit, key, type, title} = request || {};
  if (location.hostname !== 'teams.cloud.microsoft' ||
      !document.querySelector('[data-tid="me-control-avatar-trigger"]'))
    return {state: 'identity_failed'};
  const visible = element => element.getClientRects().length > 0 &&
      getComputedStyle(element).visibility !== 'hidden' &&
      element.getAttribute('aria-hidden') !== 'true';
  const isMessageSuggestion = element => {
    for (let node = element; node; node = node.parentElement) {
      const tid = node.getAttribute('data-tid') || '';
      if (/MESSAGEDOMAIN|SUGGESTION_MESSAGE/i.test(tid)) return true;
      if (node.getAttribute('role') === 'option' &&
          /^message\b/i.test(node.getAttribute('aria-label') || '')) return true;
    }
    return false;
  };
  const label = element => isMessageSuggestion(element) ? '' :
      (element.getAttribute('aria-label') || element.getAttribute('title') || '').trim().slice(0, 120);
  const meta = element => ({
    tag: element.tagName.toLowerCase(),
    id: element.id || '',
    role: element.getAttribute('role') || '',
    tid: element.getAttribute('data-tid') || '',
    label: label(element),
    placeholder: element.getAttribute('placeholder') || '',
    expanded: element.getAttribute('aria-expanded') || '',
    controls: element.getAttribute('aria-controls') || '',
    selected: element.getAttribute('aria-selected'),
    pressed: element.getAttribute('aria-pressed'),
    current: element.getAttribute('aria-current'),
    disabled: !!element.disabled || element.getAttribute('aria-disabled') === 'true'
  });
  const editors = Array.from(document.querySelectorAll(
      '[data-tid="ckeditor"][contenteditable="true"]')).filter(visible);
  const hasDraft = editor => editor.innerText.replace(/\n/g, '').length > 0 ||
      !!editor.querySelector('img,video,table,iframe,object,a') ||
      editor.querySelectorAll('p').length > 1 || editor.querySelectorAll('br').length > 1;
  const normalize = value => (value || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase();
  const validChatId = value => value === '48:notes' ||
      /^19:[A-Za-z0-9._:-]{1,256}@(thread\.(v2|skype)|unq\.gbl\.spaces)$/.test(value || '');
  const searchInput = () => Array.from(document.querySelectorAll(
      'input#ms-searchux-input[data-tid="AUTOSUGGEST_INPUT"][role="combobox"]'))
      .find(visible) || null;
  const maxSearchCandidates = 100;
  const searchPopup = input => {
    const popupId = input?.getAttribute('aria-controls') || '';
    return (popupId && document.getElementById(popupId)) ||
        document.querySelector('[data-tid="ms-searchux-popup"]');
  };
  const queryMatches = input => typeof query === 'string' && query.length > 0 &&
      normalize(input?.value) === normalize(query);
  const emailMatches = (option, value) => {
    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailPattern.test(value || '')) return false;
    const nodes = Array.from(option.querySelectorAll('[data-tid],[data-email],[data-mail]'));
    return nodes.some(node => {
      const tid = node.getAttribute('data-tid') || '';
      if (!/email|mail/i.test(tid) && !node.hasAttribute('data-email') && !node.hasAttribute('data-mail'))
        return false;
      const candidates = [node.getAttribute('data-email'), node.getAttribute('data-mail'),
        node.getAttribute('aria-label'), node.getAttribute('title'), node.textContent];
      return candidates.some(candidate => normalize(candidate) === normalize(value));
    });
  };
  const candidatesIn = (popup, value) => {
    const options = Array.from(popup.querySelectorAll('[role="option"]')).filter(visible);
    const candidates = [];
    for (const option of options) {
      const tid = option.getAttribute('data-tid') || '';
      const aria = option.getAttribute('aria-label') || '';
      if (!/^AUTOSUGGEST_SUGGESTION_TOPHITS/i.test(tid) || isMessageSuggestion(option)) continue;
      let candidateType = null;
      if (/^person\b/i.test(aria)) candidateType = 'person';
      else if (/^group\s+chat\b/i.test(aria)) candidateType = 'group';
      if (!candidateType) continue;
      const titleNode = option.querySelector('[data-tid="AUTOSUGGEST_SUGGESTION_TITLE"]');
      const candidateTitle = titleNode ? titleNode.textContent.trim().slice(0, 120) : '';
      if (!option.id || !candidateTitle) continue;
      const labelName = candidateType === 'person'
          ? aria.replace(/^person\s+/i, '').split(/\s+\(/, 1)[0].trim()
          : '';
      const exactTitle = normalize(candidateTitle) === normalize(value);
      const exactLabel = candidateType === 'person' && normalize(labelName) === normalize(value);
      const exactEmail = candidateType === 'person' && emailMatches(option, value);
      let observedGroupId = null;
      if (candidateType === 'group') {
        const match = tid.match(/(19:[A-Za-z0-9._:-]{1,256}@(thread\.(?:v2|skype)|unq\.gbl\.spaces))$/);
        observedGroupId = match ? match[1] : null;
      }
      candidates.push({
        key: option.id, type: candidateType, title: candidateTitle,
        exact_match: exactTitle || exactLabel || exactEmail,
        match_kind: exactEmail ? 'email' : exactTitle || exactLabel ? 'title' : null,
        observedGroupId, element: option
      });
      if (candidates.length > maxSearchCandidates) break;
    }
    return {
      candidates: candidates.slice(0, maxSearchCandidates),
      truncated: candidates.length > maxSearchCandidates
    };
  };
  const candidateKey = candidate => ({
    key: candidate.key, type: candidate.type, title: candidate.title,
    exact_match: candidate.exact_match, match_kind: candidate.match_kind
  });
  const popupProgress = (popup, value) => {
    const signature = JSON.stringify(Array.from(popup.querySelectorAll(
      '[role="group"],[role="option"],[data-tid]')).filter(visible).slice(0, 100)
      .map(element => [element.getAttribute('data-tid') || '', element.getAttribute('role') || '',
        label(element)]));
    const previous = window.__officeLookupSearch;
    if (!previous || previous.query !== normalize(value) || previous.signature !== signature) {
      window.__officeLookupSearch = {query: normalize(value), started: Date.now(), signature, stable: 1};
    } else {
      previous.stable++;
    }
    return window.__officeLookupSearch;
  };
  const currentCandidates = () => {
    const publicLimit = limit == null ? 50 : Number(limit);
    if (typeof query !== 'string' || query.length < 1 || query.length > 120 ||
        !Number.isInteger(publicLimit) || publicLimit < 1 || publicLimit > 50)
      return {state: 'invalid_request'};
    const input = searchInput();
    if (!input || !queryMatches(input)) return {state: 'waiting'};
    const popup = searchPopup(input);
    if (!popup || !visible(popup)) return {state: 'waiting'};
    const scan = candidatesIn(popup, query);
    const candidates = scan.candidates;
    const progress = popupProgress(popup, query);
    const topHitGroup = Array.from(popup.querySelectorAll('[role="group"],[data-tid]')).some(element =>
      (element.getAttribute('data-tid') || '') === 'AUTOSUGGEST_GROUP_TOPHITS');
    if (!topHitGroup && candidates.length === 0)
      return Date.now() - progress.started >= 2500 && progress.stable >= 4
        ? {state: 'empty'} : {state: 'waiting'};
    if (topHitGroup && candidates.length === 0 &&
        (Date.now() - progress.started < 800 || progress.stable < 2)) return {state: 'waiting'};
    if (candidates.length > 0 &&
        (Date.now() - progress.started < 800 || progress.stable < 4)) return {state: 'waiting'};
    return {state: 'ready', candidates, truncated: scan.truncated};
  };
  if (phase === 'search_results') {
    if (window.__officeLookupSelection &&
        window.__officeLookupSelection.query !== normalize(query))
      window.__officeLookupSelection = null;
    const collected = currentCandidates();
    if (collected.state !== 'ready') return collected;
    const exactPeople = collected.candidates.filter(candidate =>
      candidate.type === 'person' && candidate.exact_match);
    const exactGroups = collected.candidates.filter(candidate =>
      candidate.type === 'group' && candidate.exact_match);
    const ordered = collected.candidates.slice().sort((left, right) =>
      Number(right.exact_match) - Number(left.exact_match) ||
      (left.type === right.type ? 0 : left.type === 'person' ? -1 : 1));
    const candidates = ordered.map(candidateKey);
    if (collected.truncated)
      return {state: 'ambiguous', candidates, truncated: true};
    if (exactPeople.length > 1 || (exactPeople.length === 0 && exactGroups.length > 1))
      return {state: 'ambiguous', candidates, truncated: false};
    if (candidates.length === 0) return {state: 'empty'};
    if (exactPeople.length === 0 && exactGroups.length === 0)
      return {state: 'ambiguous', candidates, truncated: false};
    return {state: 'search_results', candidates, truncated: false};
  }
  if (phase === 'select_result') {
    const selection = window.__officeLookupSelection;
    if (selection && selection.selected) {
      return selection.query === normalize(query) && selection.key === key &&
        selection.type === type && selection.title === title
          ? {state: 'result_selected'} : {state: 'selection_conflict'};
    }
    const collected = currentCandidates();
    if (collected.state !== 'ready') return collected;
    if (collected.truncated) return {state: 'ambiguous'};
    const candidate = collected.candidates.find(item => item.key === key &&
      item.type === type && item.title === title && item.exact_match);
    if (!candidate) return {state: 'candidate_changed'};
    const exactPeople = collected.candidates.filter(item =>
      item.type === 'person' && item.exact_match);
    const exactGroups = collected.candidates.filter(item =>
      item.type === 'group' && item.exact_match);
    const preferred = exactPeople.length === 1 ? exactPeople[0] :
      exactPeople.length > 1 ? null : exactGroups.length === 1 ? exactGroups[0] : null;
    if (!preferred || preferred.key !== candidate.key)
      return {state: 'ambiguous'};
    window.__officeLookupSelection = {
      query: normalize(query), key, type, title,
      observedGroupId: candidate.observedGroupId, selected: true,
      lastHeader: null, stableCount: 0
    };
    candidate.element.click();
    return {state: 'result_selected'};
  }
  if (phase === 'resolved_chat') {
    const selection = window.__officeLookupSelection;
    if (!selection || !selection.selected || selection.query !== normalize(query) ||
        selection.key !== key || selection.type !== type || selection.title !== title)
      return {state: 'selection_missing'};
    const header = document.querySelector('[data-tid="chat-title"]');
    if (!header || !visible(header)) return {state: 'waiting'};
    const headerTitle = header.textContent.trim();
    const wrapper = header.closest('[id^="chat-header-"]');
    const wrapperId = wrapper?.id || '';
    const chatId = wrapperId.startsWith('chat-header-') ? wrapperId.slice('chat-header-'.length) : '';
    if (!validChatId(chatId) || !headerTitle) return {state: 'waiting'};
    if (selection.type === 'person' && normalize(headerTitle) !== normalize(selection.title))
      return {state: 'waiting'};
    if (selection.type === 'group' && (!selection.observedGroupId ||
        selection.observedGroupId !== chatId)) return {state: 'waiting'};
    const currentHeader = chatId + '\u0000' + headerTitle;
    if (selection.lastHeader === currentHeader) selection.stableCount++;
    else { selection.lastHeader = currentHeader; selection.stableCount = 1; }
    if (selection.stableCount < 2) return {state: 'waiting'};
    return {state: 'chat_resolved', chat: {chat_id: chatId, title: headerTitle}};
  }
  if (phase === 'prepare_search') {
    const chatTab = document.querySelector('[data-tid="app-bar-wrapper"] button[aria-label="Chat"]');
    const otherAppSelected = !!chatTab &&
        Array.from(document.querySelectorAll('[data-tid="app-bar-wrapper"] button'))
          .some(button => button !== chatTab && (button.getAttribute('aria-selected') === 'true' ||
            button.getAttribute('aria-pressed') === 'true' || button.getAttribute('aria-current') === 'page'));
    const header = document.querySelector('[data-tid="chat-title"]');
    const editors = Array.from(document.querySelectorAll('[data-tid="ckeditor"][contenteditable="true"]'))
        .filter(visible);
    const hasDraft = editor => editor.innerText.replace(/\n/g, '').length > 0 ||
        !!editor.querySelector('img,video,table,iframe,object,a') ||
        editor.querySelectorAll('p').length > 1 || editor.querySelectorAll('br').length > 1;
    if (editors.some(hasDraft)) return {state: 'draft_exists'};
    const hasNewChat = Array.from(document.querySelectorAll('button,[role="button"]'))
        .some(element => visible(element) && /^(new chat|new message|new conversation)$/i.test(
          (element.getAttribute('aria-label') || element.getAttribute('title') || '').trim()));
    const hasChatRows = Array.from(document.querySelectorAll('[role="treeitem"]')).some(element => {
      const value = element.getAttribute('data-fui-tree-item-value') || '';
      const chatId = value.slice(value.lastIndexOf('|') + 1);
      return visible(element) && (/^19:[A-Za-z0-9._:-]{1,256}@(thread\.(v2|skype)|unq\.gbl\.spaces)$/.test(chatId) ||
        chatId === '48:notes');
    });
    const workspaceReady = !otherAppSelected && (hasChatRows || hasNewChat) &&
        (!!header || editors.length > 0 || hasNewChat);
    if (!workspaceReady)
      return {state: 'chat_workspace_not_ready'};
    const inputs = Array.from(document.querySelectorAll(
        'input#ms-searchux-input[data-tid="AUTOSUGGEST_INPUT"][role="combobox"]'))
        .filter(visible);
    if (inputs.length !== 1 || inputs[0].disabled)
      return {state: 'search_input_unavailable'};
    window.__officeLookupSearch = null;
    window.__officeLookupSelection = null;
    inputs[0].focus({preventScroll: true});
    inputs[0].select();
    return {
      state: document.activeElement === inputs[0] ? 'search_prepared' : 'search_focus_failed',
      input: {id: inputs[0].id, tid: inputs[0].getAttribute('data-tid'), role: inputs[0].getAttribute('role')}
    };
  }
  if (phase === 'open_copilot') {
    const composer = {editor_count: editors.length, dirty: editors.some(hasDraft)};
    if (composer.dirty) return {state: 'draft_exists', composer};
    const appbar = document.querySelector('[data-tid="app-bar-wrapper"]');
    const buttons = appbar ? Array.from(appbar.querySelectorAll('button,[role="button"]'))
        .filter(element => visible(element) &&
          (element.getAttribute('aria-label') || element.getAttribute('title') || '').trim() === 'Copilot')
        : [];
    if (buttons.length !== 1) return {state: 'copilot_control_ambiguous', composer};
    if (buttons[0].disabled || buttons[0].getAttribute('aria-disabled') === 'true')
      return {state: 'copilot_control_disabled', composer};
    if (buttons[0].getAttribute('aria-pressed') === 'true' ||
        buttons[0].getAttribute('aria-selected') === 'true' ||
        buttons[0].getAttribute('aria-current') === 'page')
      return {state: 'copilot_already_open', composer};
    buttons[0].click();
    return {state: 'copilot_open_clicked', composer};
  }
  if (phase !== 'diagnostics') return {state: 'unsupported_phase'};
  const inputs = Array.from(document.querySelectorAll(
      'input,textarea,[contenteditable="true"],[role="combobox"]'))
      .filter(visible).slice(0, 40).map(meta);
  const buttons = Array.from(document.querySelectorAll(
      'button,[role="button"],[role="menuitem"]'))
      .filter(visible).slice(0, 60).map(meta);
  const links = Array.from(document.querySelectorAll('a,[role="option"],[role="treeitem"]'))
      .filter(visible).slice(0, 40).map(element => ({
        ...meta(element), href: element.getAttribute('href') || ''
      }));
  const sectionLabels = new Set(['people', 'chats', 'messages', 'contacts', 'groups', 'search results']);
  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]'))
      .filter(visible).slice(0, 20).map(element => {
        const heading = (element.innerText || '').trim().slice(0, 100);
        return {...meta(element), section: sectionLabels.has(heading.toLowerCase()) ? heading : ''};
      });
  const regions = Array.from(document.querySelectorAll(
      '[role="dialog"],[role="listbox"],[role="tree"],[role="status"]'))
      .filter(visible).slice(0, 20).map(meta);
  const diagnosticInput = Array.from(document.querySelectorAll('[role="combobox"]')).find(element =>
    element.getAttribute('data-tid') === 'AUTOSUGGEST_INPUT' && visible(element));
  const controlledPopupId = diagnosticInput?.getAttribute('aria-controls') || '';
  const popup = (controlledPopupId && document.getElementById(controlledPopupId)) ||
      document.querySelector('[data-tid="ms-searchux-popup"]');
  const popupChildren = popup ? Array.from(popup.querySelectorAll('*'))
      .filter(visible).slice(0, 100).map(element => ({
        ...meta(element),
        parent_tid: element.parentElement?.getAttribute('data-tid') || '',
        href: element.getAttribute('href') || ''
      })) : [];
  return {
    state: 'diagnostics',
    page: {hostname: location.hostname, path: location.pathname},
    composer: {editor_count: editors.length, dirty: editors.some(hasDraft)},
    controls: {inputs, buttons, links, regions, headings, popup: popup ? meta(popup) : null, popupChildren}
  };
}
