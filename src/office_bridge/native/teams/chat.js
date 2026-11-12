(phase, body, chatId, target, requestId) => {
  const avatar = document.querySelector('[data-tid="me-control-avatar-trigger"]');
  if (location.hostname !== 'teams.cloud.microsoft' || !avatar) return {state: 'identity_failed'};
  const editors =
      Array.from(document.querySelectorAll('[data-tid="ckeditor"][contenteditable="true"]'))
          .filter(e => e.getClientRects().length);
  const hasDraft = e => e.innerText.replace(/\n/g, '').length > 0 ||
      !!e.querySelector('img,video,table,iframe,object,a') || e.querySelectorAll('p').length > 1 ||
      e.querySelectorAll('br').length > 1;
  if (phase === 'locate') {
    const title = document.querySelector('[data-tid="chat-title"]');
    return {
      state: title?.textContent.trim() === target &&
              !!document.getElementById('chat-header-' + chatId) ?
          'ready' :
          'navigate'
    };
  }
  if (phase === 'navigate') {
    if (editors.some(hasDraft)) return {state: 'draft_exists'};
    const tab = document.querySelector('[data-tid="app-bar-wrapper"] button[aria-label="Chat"]');
    if (tab) tab.click();
    const all =
        Array.from(document.querySelectorAll('[role="treeitem"]'))
            .filter(
                e => (e.getAttribute('data-fui-tree-item-value') || '').endsWith('|' + chatId) &&
                    Array.from(e.querySelectorAll('*'))
                        .some(n => n.children.length === 0 && n.textContent.trim() === target));
    const hits = all.filter(e => !all.some(n => n !== e && e.contains(n)));
    if (hits.length !== 1) return {state: 'waiting'};
    hits[0].click();
    return {state: 'navigated'};
  }
  const title = document.querySelector('[data-tid="chat-title"]');
  if (title?.textContent.trim() !== target || !document.getElementById('chat-header-' + chatId))
    return {state: 'waiting'};
  if (phase === 'read') {
    const messages = Array.from(document.querySelectorAll('[data-tid="chat-pane-message"]'))
                         .slice(-Number(body))
                         .map(e => {
                           let p = e, author = '', time = '';
                           for (let i = 0; p && i < 5; i++, p = p.parentElement) {
                             const a = p.querySelector('[data-tid="message-author-name"]'),
                                   t = p.querySelector('time');
                             if (a && t) {
                               author = a.textContent.trim();
                               time = t.getAttribute('datetime') || '';
                               break;
                             }
                           }
                           return {id: e.id, text: e.innerText.slice(0, 2000), author, time};
                         });
    return {state: 'read', messages};
  }
  if (phase === 'prepare') {
    if (editors.length !== 1) return {state: 'waiting'};
    if (hasDraft(editors[0])) return {state: 'draft_exists'};
    window.__officeRequest = {
      id: requestId,
      before:
          Array.from(document.querySelectorAll('[data-tid="chat-pane-message"]')).map(e => e.id),
      clicked: false
    };
    editors[0].focus();
    return {state: document.activeElement === editors[0] ? 'prepared' : 'failed'};
  }
  if (phase === 'send') {
    if (editors.length !== 1 || editors[0].innerText.trim() !== body ||
        window.__officeRequest?.id !== requestId || window.__officeRequest.clicked !== false ||
        !Array.isArray(window.__officeRequest.before))
      return {state: 'failed'};
    const buttons =
        Array
            .from(document.querySelectorAll(
                'button[data-tid="newMessageCommands-send"],button[data-tid="sendMessageCommands-send"]'))
            .filter(
                e => e.getClientRects().length && !e.disabled &&
                    e.getAttribute('aria-disabled') !== 'true');
    if (buttons.length !== 1) return {state: 'waiting'};
    window.__officeRequest.clicked = true;
    buttons[0].click();
    return {state: 'clicked'};
  }
  if (phase === 'observe') {
    if (window.__officeRequest?.id !== requestId || window.__officeRequest.clicked !== true)
      return {state: 'failed'};
    const matches =
        Array.from(document.querySelectorAll('[data-tid="chat-pane-message"]'))
            .filter(
                e => !window.__officeRequest.before.includes(e.id) && e.innerText.trim() === body &&
                    /^message-body-[0-9]{13}$/.test(e.id));
    if (matches.length !== 1) return {state: 'waiting'};
    const e = matches[0], mid = e.id.slice('message-body-'.length);
    if (e.getAttribute('data-mid') !== mid) return {state: 'waiting'};
    let p = e;
    for (let i = 0; p && i < 5; i++, p = p.parentElement) {
      const author = p.querySelector('[data-tid="message-author-name"]');
      const stamp = p.querySelector('time');
      if (author && stamp && author.textContent.trim() === 'Yorko, Joshua' &&
          Date.parse(stamp.getAttribute('datetime')) === Number(mid))
        return {state: 'observed', message_id: e.id, time: stamp.getAttribute('datetime')};
    }
    return {state: 'waiting'};
  }
  return {state: 'failed'};
}
