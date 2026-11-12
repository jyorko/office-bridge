(request) => {
  const {phase, chatId, title, limit, name, requestId, body, authorName, navigationId} = request;
  const avatar = document.querySelector('[data-tid="me-control-avatar-trigger"]');
  if (location.hostname !== 'teams.cloud.microsoft' || !avatar) return {state: 'identity_failed'};
  const validId = id => id === '48:notes' || /^19:[A-Za-z0-9._:-]{1,256}@thread\.(v2|skype)$/.test(id || '');
  const rowId = row => {
    const value = row.getAttribute('data-fui-tree-item-value') || '';
    const id = value.slice(value.lastIndexOf('|') + 1);
    return validId(id) ? id : null;
  };
  const rowTitle = row => {
    const preferred = row.querySelector('[data-tid="chat-list-item-title"],[data-tid="chat-list-item-name"]');
    if (preferred && preferred.textContent.trim()) return preferred.textContent.trim();
    const leaves = Array.from(row.querySelectorAll('*'))
        .filter(e => e.children.length === 0 && e.textContent.trim())
        .map(e => e.textContent.trim())
        .filter(t => t.length <= 120 && !/^\d{1,2}:\d{2}\s?(AM|PM)?$/i.test(t));
    return leaves[0] || '';
  };
  const rows = () => Array.from(document.querySelectorAll('[role="treeitem"]'))
      .filter(e => rowId(e) && rowTitle(e))
      .filter(e => !Array.from(e.querySelectorAll('[role="treeitem"]')).some(n => rowId(n)));
  const exactRows = () => rows().filter(e => rowTitle(e) === title);
  const matchingRows = () => exactRows().filter(e => rowId(e) === chatId);
  const header = document.querySelector('[data-tid="chat-title"]');
  const headerMatches = () => !!header && header.textContent.trim() === title &&
      !!document.getElementById('chat-header-' + chatId);
  const editors = Array.from(document.querySelectorAll('[data-tid="ckeditor"][contenteditable="true"]'))
      .filter(e => e.getClientRects().length);
  const hasDraft = e => e.innerText.replace(/\n/g, '').length > 0 ||
      !!e.querySelector('img,video,table,iframe,object,a') || e.querySelectorAll('p').length > 1 ||
      e.querySelectorAll('br').length > 1;
  const openChatListOnce = () => {
    if (!window.__officeNavigation || window.__officeNavigation.id !== navigationId)
      window.__officeNavigation = {id: navigationId, opened: false};
    const tab = document.querySelector('[data-tid="app-bar-wrapper"] button[aria-label="Chat"]');
    if (tab && !window.__officeNavigation.opened) {
      window.__officeNavigation.opened = true;
      if (tab.getAttribute('aria-selected') !== 'true' && tab.getAttribute('aria-pressed') !== 'true')
        tab.click();
    }
  };
  if (phase === 'list') {
    if (!editors.some(hasDraft)) openChatListOnce();
    const chatRows = rows();
    if (!chatRows.length) return {state: 'waiting'};
    const found = chatRows.map(row => ({chat_id: rowId(row), title: rowTitle(row)}))
        .filter(item => !name || item.title === name);
    const unique = found.filter((item, index) =>
      found.findIndex(other => other.chat_id === item.chat_id) === index);
    const count = unique.length;
    return {state: 'listed', items: unique.slice(0, limit), truncated: count > limit};
  }
  if (phase === 'locate')
    return headerMatches() && matchingRows().length === 1 ? {state: 'ready'} : {state: 'navigate'};
  if (phase === 'navigate') {
    if (editors.some(hasDraft)) return {state: 'draft_exists'};
    openChatListOnce();
    const hits = matchingRows();
    if (hits.length !== 1) return {state: 'waiting'};
    hits[0].click();
    return {state: 'navigated'};
  }
  if (!headerMatches() || matchingRows().length !== 1)
    return {state: 'waiting'};
  if (phase === 'read') {
    const messages = Array.from(document.querySelectorAll('[data-tid="chat-pane-message"]'))
        .slice(-Number(limit))
        .map(e => {
          let p = e, author = '', time = '';
          for (let i = 0; p && i < 5; i++, p = p.parentElement) {
            const a = p.querySelector('[data-tid="message-author-name"]');
            const t = p.querySelector('time');
            if (a && t) { author = a.textContent.trim(); time = t.getAttribute('datetime') || ''; break; }
          }
          return {id: e.id, text: e.innerText.slice(0, 2000), author, time};
        });
    return {state: 'read', chat_id: chatId, title, messages};
  }
  if (phase === 'prepare') {
    if (editors.length !== 1) return {state: 'waiting'};
    if (hasDraft(editors[0])) return {state: 'draft_exists'};
    window.__officeRequest = {
      id: requestId,
      before: Array.from(document.querySelectorAll('[data-tid="chat-pane-message"]')).map(e => e.id),
      clicked: false
    };
    editors[0].focus();
    return {state: document.activeElement === editors[0] ? 'prepared' : 'failed'};
  }
  if (phase === 'send') {
    if (editors.length !== 1 || editors[0].innerText.trim() !== body ||
        window.__officeRequest?.id !== requestId || window.__officeRequest.clicked !== false ||
        !Array.isArray(window.__officeRequest.before)) return {state: 'failed'};
    const buttons = Array.from(document.querySelectorAll(
        'button[data-tid="newMessageCommands-send"],button[data-tid="sendMessageCommands-send"]'))
        .filter(e => e.getClientRects().length && !e.disabled && e.getAttribute('aria-disabled') !== 'true');
    if (buttons.length !== 1) return {state: 'waiting'};
    window.__officeRequest.clicked = true;
    buttons[0].click();
    return {state: 'clicked'};
  }
  if (phase === 'observe') {
    if (window.__officeRequest?.id !== requestId || window.__officeRequest.clicked !== true || !authorName)
      return {state: 'failed'};
    const matches = Array.from(document.querySelectorAll('[data-tid="chat-pane-message"]'))
        .filter(e => !window.__officeRequest.before.includes(e.id) && e.innerText.trim() === body &&
            /^message-body-[0-9]{13}$/.test(e.id));
    if (matches.length !== 1) return {state: 'waiting'};
    const e = matches[0], mid = e.id.slice('message-body-'.length);
    if (e.getAttribute('data-mid') !== mid) return {state: 'waiting'};
    let p = e;
    for (let i = 0; p && i < 5; i++, p = p.parentElement) {
      const author = p.querySelector('[data-tid="message-author-name"]');
      const stamp = p.querySelector('time');
      if (author && stamp && author.textContent.trim() === authorName &&
          Date.parse(stamp.getAttribute('datetime')) === Number(mid))
        return {state: 'observed', message_id: e.id, time: stamp.getAttribute('datetime'), chat_id: chatId, title};
    }
    return {state: 'waiting'};
  }
  return {state: 'failed'};
}
