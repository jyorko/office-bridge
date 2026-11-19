(request, renderNodes) => {
  const {phase, chatId, title, limit, name, requestId, body, bodyFormat, nodes, formatting,
    authorName, navigationId, messageId, menuAction, expectedText, mentionDiagnostics,
    mentions, visibleBody, mention} = request;
  const avatar = document.querySelector('[data-tid="me-control-avatar-trigger"]');
  if (location.hostname !== 'teams.cloud.microsoft' || !avatar) return {state: 'identity_failed'};
  const validId = id => id === '48:notes' || /^19:[A-Za-z0-9._:-]{1,256}@(thread\.(v2|skype)|unq\.gbl\.spaces)$/.test(id || '');
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
        .filter(t => t.length <= 120 && t !== 'Unread' &&
          !/^\d{1,2}:\d{2}\s?(AM|PM)?$/i.test(t));
    return leaves[0] || '';
  };
  const rows = () => Array.from(document.querySelectorAll('[role="treeitem"]'))
      .filter(e => rowId(e) && rowTitle(e))
      .filter(e => !Array.from(e.querySelectorAll('[role="treeitem"]')).some(n => rowId(n)));
  // Rail text includes badges and previews. Navigate by its stable ID; the opened
  // header below independently verifies both the exact ID and requested title.
  const matchingRows = () => rows().filter(e => rowId(e) === chatId);
  const header = document.querySelector('[data-tid="chat-title"]');
  const headerMatches = () => !!header && header.textContent.trim() === title &&
      header.closest('[id^="chat-header-"]')?.id === 'chat-header-' + chatId;
  const messageBeingEdited = message => {
    const item = message.closest('[data-tid="chat-pane-item"]') || message;
    return !!item.querySelector('[contenteditable="true"],button[data-tid="newMessageCommands-send"][aria-label="Done"]');
  };
  const editors = Array.from(document.querySelectorAll('[data-tid="ckeditor"][contenteditable="true"]'))
      .filter(e => e.getClientRects().length);
  const composerRoot = editor => {
    const selector = 'button[data-tid="newMessageCommands-send"],button[data-tid="sendMessageCommands-send"]';
    let current = editor;
    for (let depth = 0; current && depth < 12; depth++, current = current.parentElement) {
      if (current === document.body || current === document.documentElement) return null;
      if (current.querySelector(selector)) return current;
    }
    return null;
  };
  const pendingComposerFiles = editor => {
    if (Array.from(document.querySelectorAll('input[type="file"]'))
      .some(input => input.files?.length)) return true;
    const root = composerRoot(editor);
    if (!root) return false;
    if (Array.from(root.querySelectorAll('input[type="file"]')).some(input => input.files?.length))
      return true;
    const textOnly = root.cloneNode(true);
    textOnly.querySelectorAll(
      '[data-tid="ckeditor"],[contenteditable="true"],button,[role="button"],input'
    ).forEach(element => element.remove());
    const attachmentText = normalizeText(textOnly.innerText || textOnly.textContent);
    const namedFile = /(?:^|\s)[^\s<>:"/\\|?*]{1,180}\.[A-Za-z0-9]{1,16}(?:$|\s)/i
      .test(attachmentText);
    const uploading = root.querySelector(
      '[role="progressbar"],[aria-busy="true"],[data-tid*="upload-progress"],[data-tid*="file-progress"]'
    );
    return namedFile || !!uploading;
  };
  const draftKind = e => {
    if (e.innerText.replace(/[\n\u2060]/g, '').length) return 'text';
    if (e.querySelector('mention,[itemtype="http://schema.skype.com/Mention"],img,video,table,iframe,object,a') ||
        e.querySelectorAll('p').length > 1 || e.querySelectorAll('br').length > 1) return 'markup';
    return pendingComposerFiles(e) ? 'attachment' : '';
  };
  const hasDraft = e => !!draftKind(e);
  const visible = element => element.getClientRects().length > 0 &&
      getComputedStyle(element).visibility !== 'hidden';
  const chatTab = () => document.querySelector('[data-tid="app-bar-wrapper"] button[aria-label="Chat"]');
  const chatTabSelected = tab => !!tab && (tab.getAttribute('aria-selected') === 'true' ||
      tab.getAttribute('aria-pressed') === 'true' || tab.getAttribute('aria-current') === 'page');
  const anotherAppSelected = tab => !!tab &&
      Array.from(document.querySelectorAll('[data-tid="app-bar-wrapper"] button'))
        .some(button => button !== tab && (button.getAttribute('aria-selected') === 'true' ||
          button.getAttribute('aria-pressed') === 'true' || button.getAttribute('aria-current') === 'page'));
  const newChatControl = () => Array.from(document.querySelectorAll('button,[role="button"]'))
      .some(element => element.getClientRects().length &&
        /^(new chat|new message|new conversation)$/i.test(
          (element.getAttribute('aria-label') || element.getAttribute('title') || '').trim()));
  const chatWorkspaceReady = tab => {
    const railReady = rows().length > 0 || newChatControl();
    const currentReady = !!header || editors.length > 0 || newChatControl();
    return !anotherAppSelected(tab) && railReady && currentReady;
  };
  const normalizeText = value => (value || '').replace(/\u2060/g, '').replace(/\s+/g, ' ').trim();
  const formatKeys = ['bold', 'italic', 'code', 'links', 'unordered_list', 'ordered_list'];
  const emptyFormatting = formatting => {
    if (!formatting || typeof formatting !== 'object' ||
        formatKeys.some(key => !Object.prototype.hasOwnProperty.call(formatting, key))) return false;
    return ['bold', 'italic', 'code', 'links'].every(key =>
        Array.isArray(formatting[key]) && formatting[key].length === 0) &&
        formatting.unordered_list === false && formatting.ordered_list === false;
  };
  const plainProjection = node => {
    const blockTags = new Set(['blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'ol', 'p', 'pre', 'ul']);
    let text = '';
    const walk = current => {
      if (current.nodeType === Node.TEXT_NODE) { text += current.nodeValue; return; }
      if (current.nodeType !== Node.ELEMENT_NODE && current.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;
      const block = current.nodeType === Node.ELEMENT_NODE && blockTags.has(current.tagName.toLowerCase());
      if (block && text && !text.endsWith('\n')) text += '\n';
      if (current.nodeType === Node.ELEMENT_NODE && current.tagName.toLowerCase() === 'br') text += '\n';
      else for (const child of current.childNodes) walk(child);
      if (block && !text.endsWith('\n')) text += '\n';
    };
    walk(node);
    return normalizeText(text);
  };
  const matchingFormatting = (root, formatting) => {
    if (!formatting || typeof formatting !== 'object' ||
        !Array.isArray(formatting.bold) || !Array.isArray(formatting.italic) ||
        !Array.isArray(formatting.code) || !Array.isArray(formatting.links) ||
        ![...formatting.bold, ...formatting.italic, ...formatting.code].every(x => typeof x === 'string') ||
        !formatting.links.every(x => x && typeof x.text === 'string' && typeof x.href === 'string') ||
        typeof formatting.unordered_list !== 'boolean' || typeof formatting.ordered_list !== 'boolean')
      return false;
    const values = (selector, property='textContent') => Array.from(root.querySelectorAll(selector))
        .map(e => (e[property] || '').trim()).sort();
    const sorted = items => items.slice().sort();
    const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);
    const links = Array.from(root.querySelectorAll('a')).map(e => ({
      text: (e.textContent || '').trim(), href: e.getAttribute('href') || ''
    })).sort((a, b) => a.href.localeCompare(b.href) || a.text.localeCompare(b.text));
    const expectedLinks = formatting.links.map(link => ({text: link.text, href: link.href}))
        .sort((a, b) => a.href.localeCompare(b.href) || a.text.localeCompare(b.text));
    return equal(values('strong,b'), sorted(formatting.bold)) &&
        equal(values('em,i'), sorted(formatting.italic)) &&
        equal(values('code'), sorted(formatting.code)) && equal(links, expectedLinks) &&
        (!!root.querySelector('ul') === formatting.unordered_list) &&
        (!!root.querySelector('ol') === formatting.ordered_list);
  };
  const validatedFragment = () => {
    if (bodyFormat === 'plain')
      return nodes == null && emptyFormatting(formatting) ? {valid: true, template: null} : null;
    if (bodyFormat !== 'markdown' || !Array.isArray(nodes) ||
        typeof renderNodes !== 'function' || !formatting)
      return null;
    const template = renderNodes(nodes);
    if (!(template instanceof HTMLTemplateElement)) return null;
    const allowed = new Set(['a','blockquote','br','code','em','h1','h2','h3','h4','h5','h6','hr','li','ol','p','pre','strong','ul']);
    for (const element of template.content.querySelectorAll('*')) {
      const tag = element.tagName.toLowerCase();
      if (!allowed.has(tag)) return null;
      for (const attr of Array.from(element.attributes)) {
        if (tag === 'a' && (attr.name === 'href' || attr.name === 'title')) continue;
        if (tag === 'ol' && attr.name === 'start' && /^\d+$/.test(attr.value)) continue;
        return null;
      }
      if (tag === 'a') {
        const href = element.getAttribute('href') || '';
        try {
          const url = new URL(href);
          if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password)
            return null;
        } catch (_) { return null; }
      }
    }
    const text = plainProjection(template.content);
    if (text !== normalizeText(body) || !matchingFormatting(template.content, formatting)) return null;
    return {valid: true, template};
  };
  const renderedMatches = (root, canonicalBody, formatting) =>
      normalizeText(root.innerText || root.textContent) === normalizeText(canonicalBody) &&
      matchingFormatting(root, formatting);
  const mentionIdentityAttributes = element => {
    const names = /email|mail|upn|principal|mri|itemid|recipient|user|object|identity|entity|contact|member|person|account/i;
    const values = new Set();
    const elements = [element, ...Array.from(element.querySelectorAll('*'))];
    for (const node of elements) {
      // Teams puts the picker's organizational MRI in the option DOM ID.
      // Bind that exact UUID on the same option whose address was verified.
      const domId = (node.getAttribute('id') || '').replace(/%3a/ig, ':');
      const pickerMri = domId.match(/(?:^|[^A-Za-z0-9])(8:orgid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?=$|[^0-9a-f])/i);
      if (pickerMri) values.add(pickerMri[1].toLowerCase());
      for (const attribute of Array.from(node.attributes || [])) {
        const value = attribute.value.trim();
        // Bind a person through an explicit address or stable organizational MRI.
        // Generic user labels and unrelated attribute values cannot establish identity.
        if (names.test(attribute.name) &&
            (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value) ||
             /^8:orgid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)))
          values.add(value.toLowerCase());
      }
    }
    return Array.from(values).sort();
  };
  const mentionTextLabel = (element, email) => normalizeText(
    (element.getAttribute('aria-label') || element.innerText || element.textContent || '')
      .replace(new RegExp(email.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'ig'), '')
      .replace(/^[\s,·•|()-]+|[\s,·•|()-]+$/g, '')
  );
  const mentionCandidateMatches = (email, label) => {
    const options = Array.from(document.querySelectorAll('[role="option"]')).filter(visible);
    const matched = options.filter(option => {
      const identity = mentionIdentityAttributes(option);
      const text = normalizeText(option.getAttribute('aria-label') || option.innerText || option.textContent || '');
      const emailInText = new RegExp(
        '(^|[^A-Za-z0-9._%+-])' + email.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') +
        '($|[^A-Za-z0-9._%+-])', 'i'
      ).test(text);
      return identity.some(value => value.toLowerCase() === email.toLowerCase()) || emailInText;
    }).filter(option => {
      const optionLabel = mentionTextLabel(option, email);
      return !label || optionLabel === normalizeText(label);
    });
    return matched.map(option => ({
      option,
      label: mentionTextLabel(option, email),
      identity: mentionIdentityAttributes(option)
    }));
  };
  const mentionEntities = root => Array.from(root.querySelectorAll('*')).filter(element => {
    const tag = element.tagName.toLowerCase();
    const itemtype = element.getAttribute('itemtype') || '';
    const tid = element.getAttribute('data-tid') || '';
    if (tag === 'mention')
      return !element.parentElement?.closest('[itemtype="http://schema.skype.com/Mention"]');
    if (/\/Mention$/i.test(itemtype))
      return !element.parentElement?.closest('[itemtype="http://schema.skype.com/Mention"]');
    return /mention/i.test(tid) && element.getAttribute('contenteditable') === 'false' &&
      !element.closest('[itemtype="http://schema.skype.com/Mention"]');
  });
  const mentionEntityMatches = (entity, expected) => {
    const text = normalizeText(entity.innerText || entity.textContent || '');
    if (text !== normalizeText(expected.label)) return false;
    const identity = mentionIdentityAttributes(entity);
    const direct = identity.some(value => value.toLowerCase() === expected.email.toLowerCase());
    const shared = identity.some(value => expected.identity.includes(value));
    return direct || shared;
  };
  const uploadInputs = () => Array.from(document.querySelectorAll('input[type="file"]'));
  const attachControls = root => Array.from(
    (root || document).querySelectorAll('button,[role="button"]')
  ).filter(element => visible(element) && !element.disabled &&
    element.getAttribute('aria-disabled') !== 'true' &&
    (/^(attach|attachments|attach files|attach a file|add attachment|add file|add files|upload file)$/i
      .test(normalizeText(element.getAttribute('aria-label') || element.getAttribute('title') ||
        element.innerText || element.textContent)) ||
      /(?:^|[-_])attachment(?:[-_]|$)/i.test(element.getAttribute('data-tid') || '')));
  const uploadNamesVisible = (root, files) => {
    if (!root) return false;
    const labels = Array.from(root.querySelectorAll('[aria-label],[title]'))
      .flatMap(element => [element.getAttribute('aria-label') || '', element.getAttribute('title') || '']);
    const surface = normalizeText((root.innerText || root.textContent || '') + ' ' + labels.join(' '));
    return files.every(file => surface.includes(file.name));
  };
  const uploadReady = files => {
    if (editors.length !== 1 || hasDraftText(editors[0])) return false;
    const root = composerRoot(editors[0]);
    if (!root || !uploadNamesVisible(root, files)) return false;
    const pending = root.querySelector(
      '[role="progressbar"],[aria-busy="true"],[data-tid*="upload-progress"],[data-tid*="file-progress"]'
    );
    const buttons = Array.from(root.querySelectorAll(
      'button[data-tid="newMessageCommands-send"],button[data-tid="sendMessageCommands-send"]'
    )).filter(element => element.getClientRects().length);
    return !pending && buttons.length === 1 && !buttons[0].disabled &&
      buttons[0].getAttribute('aria-disabled') !== 'true';
  };
  const hasDraftText = editor => editor.innerText.replace(/[\n\u2060]/g, '').length > 0 ||
      !!editor.querySelector('mention,[itemtype="http://schema.skype.com/Mention"]') ||
      !!editor.querySelector('img,video,table,iframe,object,a') || editor.querySelectorAll('p').length > 1 ||
      editor.querySelectorAll('br').length > 1;
  if (phase === 'validate_payload')
    return validatedFragment() ? {state: 'payload_valid'} : {state: 'invalid_format'};
  const openChatListOnce = () => {
    if (!window.__officeNavigation || window.__officeNavigation.id !== navigationId)
      window.__officeNavigation = {id: navigationId, opened: false};
    const tab = chatTab();
    if (tab && !window.__officeNavigation.opened) {
      window.__officeNavigation.opened = true;
      if (!chatTabSelected(tab) && !chatWorkspaceReady(tab)) {
        tab.click();
        return true;
      }
    }
    return false;
  };
  if (phase === 'workspace_ready') {
    if (!window.__officeWorkspace || window.__officeWorkspace.id !== navigationId)
      window.__officeWorkspace = {id: navigationId, opened: false};
    const tab = chatTab();
    const selected = chatTabSelected(tab);
    if (chatWorkspaceReady(tab))
      return {state: 'workspace_ready', selected, selection_state: selected ? 'selected' : 'unknown',
        sidebar_ready: rows().length > 0, has_header: !!header, editor_count: editors.length,
        dirty: editors.some(hasDraft), new_chat_control: newChatControl()};
    if (tab && !selected && !window.__officeWorkspace.opened) {
      window.__officeWorkspace.opened = true;
      tab.click();
      return {state: 'workspace_opening'};
    }
    return {state: 'waiting', selected, selection_state: selected ? 'selected' : tab ? 'unknown' : 'missing',
      other_app_selected: anotherAppSelected(tab), sidebar_ready: rows().length > 0,
      has_header: !!header, editor_count: editors.length, dirty: editors.some(hasDraft),
      new_chat_control: newChatControl()};
  }
  if (phase === 'list') {
    if (!editors.some(hasDraft) && openChatListOnce()) return {state: 'waiting'};
    const chatRows = rows();
    if (!chatRows.length) return {state: 'waiting'};
    const found = chatRows.map(row => ({chat_id: rowId(row), title: rowTitle(row)}))
        .filter(item => !name || item.title === name);
    const unique = found.filter((item, index) =>
      found.findIndex(other => other.chat_id === item.chat_id) === index);
    const count = unique.length;
    return {state: 'listed', items: unique.slice(0, limit), truncated: count > limit};
  }
  if (phase === 'snapshot') {
    const mix = value => {
      let a = 2166136261, b = 2246822519;
      for (let i = 0; i < value.length; i++) {
        const code = value.charCodeAt(i);
        a = Math.imul(a ^ code, 16777619);
        b = Math.imul(b + code, 3266489917);
      }
      return [a >>> 0, b >>> 0, value.length].join(':');
    };
    const signature = {
      path: mix(location.href),
      chatHeader: header ? mix(header.textContent.trim()) : '',
      headerId: mix(header?.closest('[id^="chat-header-"]')?.id || ''),
      editors: Array.from(document.querySelectorAll('[data-tid="ckeditor"][contenteditable="true"],[data-tid="chat-pane-item"] [contenteditable="true"]')).map(editor => ({
        id: mix(editor.id),
        text: mix(editor.innerText || ''),
        html: mix(editor.innerHTML),
        active: document.activeElement === editor,
        selection: (() => {
          const selection = window.getSelection();
          if (!selection || !editor.contains(selection.anchorNode)) return null;
          return [selection.anchorNode?.nodeType || 0, selection.anchorOffset,
            selection.focusNode?.nodeType || 0, selection.focusOffset];
        })()
      }))
    };
    const inlineEdit = !!document.querySelector('[data-tid="chat-pane-item"] [contenteditable="true"],[data-tid="chat-pane-item"] button[data-tid="newMessageCommands-send"][aria-label="Done"]');
    const draft = inlineEdit ? 'inline_edit' : editors.map(draftKind).find(Boolean) || '';
    return {state: 'snapshot', fingerprint: JSON.stringify(signature),
      editor_count: editors.length, dirty: !!draft, draft_kind: draft};
  }
  if (phase === 'mention_probe_draft_status') {
    return {state: 'draft_exists', exact_chat: headerMatches(), editor_count: editors.length,
      query_matches: editors.length === 1 && typeof mention?.recipient_email === 'string' &&
        normalizeText(editors[0].innerText).toLowerCase() ===
          ('@' + mention.recipient_email).toLowerCase(),
      text_length: editors.length === 1 ? normalizeText(editors[0].innerText).length : 0,
      paragraphs: editors.length === 1 ? editors[0].querySelectorAll('p').length : 0,
      breaks: editors.length === 1 ? editors[0].querySelectorAll('br').length : 0,
      address_only: editors.length === 1 &&
        normalizeText(editors[0].innerText).toLowerCase() === mention?.recipient_email?.toLowerCase(),
      query_repeated: editors.length === 1 && typeof mention?.recipient_email === 'string' &&
        normalizeText(editors[0].innerText).toLowerCase() ===
          ('@' + mention.recipient_email + '@' + mention.recipient_email).toLowerCase(),
      entity_count: editors.length === 1 ? mentionEntities(editors[0]).length : 0,
      editor_text: editors.length === 1 ? normalizeText(editors[0].innerText).slice(0, 250) : '',
      entities: editors.length === 1 ? mentionEntities(editors[0]).map(entity => ({
        tag: entity.tagName.toLowerCase(),
        label: normalizeText(entity.innerText || entity.textContent).slice(0, 120),
        attributes: Array.from(entity.attributes).map(attribute => attribute.name),
        identities: mentionIdentityAttributes(entity)
      })) : []};
  }
  if (phase === 'mention_probe_recover') {
    const entities = editors.length === 1 ? mentionEntities(editors[0]) : [];
    if (!headerMatches() || editors.length !== 1 || entities.length !== 1 ||
        typeof request.expectedProbeText !== 'string' ||
        normalizeText(editors[0].innerText) !== normalizeText(request.expectedProbeText) ||
        typeof request.expectedProbeMri !== 'string' ||
        !/^8:orgid:[0-9a-f-]{36}$/i.test(request.expectedProbeMri) ||
        !mentionIdentityAttributes(entities[0]).includes(request.expectedProbeMri.toLowerCase()) ||
        pendingComposerFiles(editors[0]) ||
        editors[0].querySelector('img,video,table,iframe,object,a'))
      return {state: 'probe_recovery_changed'};
    const label = normalizeText(entities[0].innerText || entities[0].textContent);
    if (normalizeText(editors[0].innerText) !== label)
      return {state: 'probe_recovery_changed'};
    window.__officeMentionProbe = {
      recipient: mention.recipient_email.toLowerCase(), editor: editors[0], before: '',
      selected: {email: mention.recipient_email.toLowerCase(), label,
        identity: [request.expectedProbeMri.toLowerCase()]},
      entity: entities[0]
    };
    return {state: 'probe_recovery_prepared'};
  }
  if (phase === 'mention_probe_prepare') {
    if (!headerMatches() || editors.length !== 1 || hasDraft(editors[0]) ||
        typeof mention?.recipient_email !== 'string' ||
        !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(mention.recipient_email))
      return {state: editors.some(hasDraft) ? 'draft_exists' : 'probe_unavailable'};
    window.__officeMentionProbe = {
      recipient: mention.recipient_email.toLowerCase(),
      query: '@' + (request.queryText || mention.recipient_email),
      editor: editors[0],
      before: editors[0].innerHTML,
      candidate: null,
      selected: null
    };
    editors[0].focus();
    return {state: document.activeElement === editors[0] ? 'probe_prepared' : 'probe_unavailable'};
  }
  if (phase === 'mention_probe_focus') {
    const probe = window.__officeMentionProbe;
    if (!probe || editors.length !== 1 || editors[0] !== probe.editor ||
        !headerMatches() || hasDraft(editors[0]))
      return {state: 'probe_changed'};
    editors[0].focus();
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(editors[0]);
    range.collapse(false);
    selection.removeAllRanges();
    selection.addRange(range);
    return {state: document.activeElement === editors[0] ? 'probe_focused' : 'probe_unavailable'};
  }
  if (phase === 'mention_probe_options') {
    const probe = window.__officeMentionProbe;
    if (!probe || editors.length !== 1 || editors[0] !== probe.editor ||
        !headerMatches())
      return {state: 'probe_changed'};
    if (!editors[0].innerText.trim())
      return {state: 'waiting', text_length: 0, editor_focused: document.activeElement === editors[0]};
    const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[role="alertdialog"]'))
      .filter(visible);
    if (dialogs.some(dialog => /\b(join|add|invite)\b.{0,35}\b(chat|conversation|people)\b/i
      .test(normalizeText(dialog.innerText || dialog.textContent))))
      return {state: 'membership_prompt'};
    const matches = mentionCandidateMatches(probe.recipient, '');
    const options = Array.from(document.querySelectorAll('[role="option"]')).filter(visible);
    const contactShare = option => /^share someone.s contact info$/i.test(
      normalizeText(option.innerText || option.textContent));
    if (options.length && options.every(contactShare))
      return {state: 'no_person_candidates', option_count: options.length,
        option_kind: 'contact_share'};
    if (matches.length !== 1)
      return options.length ? {state: 'identity_unverified', option_count: options.length,
        options: options.slice(0, 10).map(option => ({
          option_kind: contactShare(option) ? 'contact_share' : 'unclassified',
          text_length: normalizeText(option.innerText || option.textContent).length,
          schema_types: Array.from(new Set([option, ...option.querySelectorAll('[itemtype]')]
            .map(node => node.getAttribute('itemtype'))
            .filter(value => /^https?:\/\/(?:schema\.skype\.com|schema\.org)\/(?:Person|Contact|Mention|Organization)$/i.test(value || '')))),
          ids_contain_mri: [option, ...option.querySelectorAll('[id]')]
            .some(node => /8(?::|%3a)orgid(?::|%3a)[0-9a-f-]{36}/i.test(node.id)),
          label_matches_account: normalizeText(option.innerText || option.textContent) ===
            normalizeText(request.accountLabel || ''),
          aria_label_matches_account: normalizeText(option.getAttribute('aria-label')) ===
            normalizeText(request.accountLabel || ''),
          attribute_names: Array.from(new Set([option, ...option.querySelectorAll('*')]
            .flatMap(node => Array.from(node.attributes).map(attribute => attribute.name)))).sort(),
          identity_count: mentionIdentityAttributes(option).length
        }))} :
        {state: 'waiting', text_length: normalizeText(editors[0].innerText).length,
          editor_focused: document.activeElement === editors[0],
          surfaces: Array.from(document.querySelectorAll(
            '[role="listbox"],[role="menu"],[data-tid*="mention"],[data-tid*="suggestion"]'))
            .filter(visible).slice(0, 30).map(node => ({
              tag: node.tagName.toLowerCase(),
              role: node.getAttribute('role') || '',
              tid: (node.getAttribute('data-tid') || '').replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/ig, '[id]'),
              children: Array.from(node.querySelectorAll('[role],[data-tid]')).slice(0, 30)
                .map(child => ({
                  tag: child.tagName.toLowerCase(), role: child.getAttribute('role') || '',
                  tid: (child.getAttribute('data-tid') || '').replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/ig, '[id]'),
                  attributes: Array.from(child.attributes).map(attribute => attribute.name),
                  email_visible: normalizeText(child.innerText || child.textContent)
                    .toLowerCase().includes(probe.recipient)
                }))
            }))};
    const candidate = matches[0];
    if (!candidate.label)
      return {state: 'identity_unverified', option_count: options.length};
    probe.candidate = candidate;
    return {
      state: 'candidate_verified',
      display_label: candidate.label,
      option_count: options.length,
      identity_attribute_count: candidate.identity.length
    };
  }
  if (phase === 'mention_probe_select') {
    const probe = window.__officeMentionProbe;
    if (!probe || !probe.candidate || !headerMatches() || editors.length !== 1 ||
        editors[0] !== probe.editor)
      return {state: 'probe_changed', phase, probe_exists: !!probe,
        header_matches: headerMatches(), editor_count: editors.length,
        editor_same: editors[0] === probe?.editor};
    const matches = mentionCandidateMatches(probe.recipient, probe.candidate.label);
    if (matches.length !== 1 || matches[0].option !== probe.candidate.option ||
        JSON.stringify(matches[0].identity) !== JSON.stringify(probe.candidate.identity))
      return {state: 'candidate_changed'};
    const option = matches[0].option;
    if (option.getAttribute('aria-disabled') === 'true' || option.disabled)
      return {state: 'candidate_disabled'};
    option.click();
    probe.selected = {
      email: probe.recipient,
      label: probe.candidate.label,
      identity: probe.candidate.identity
    };
    return {state: 'candidate_selected'};
  }
  if (phase === 'mention_probe_chip') {
    const probe = window.__officeMentionProbe;
    if (!probe || !probe.selected || editors.length !== 1 ||
        editors[0] !== probe.editor || !headerMatches())
      return {state: 'probe_changed', phase, probe_exists: !!probe,
        header_matches: headerMatches(), editor_count: editors.length,
        editor_same: editors[0] === probe?.editor};
    const matching = mentionEntities(editors[0])
      .filter(entity => mentionEntityMatches(entity, probe.selected));
    if (matching.length !== 1) return {state: 'chip_unverified',
      entity_count: mentionEntities(editors[0]).length,
      entities: mentionEntities(editors[0]).map(entity => ({
        label: normalizeText(entity.innerText || entity.textContent || ''),
        attributes: Array.from(entity.attributes).map(attribute => attribute.name),
        identity_count: mentionIdentityAttributes(entity).length
      }))};
    const entity = matching[0];
    const attributeNames = Array.from(entity.attributes || [])
      .map(attribute => attribute.name)
      .filter(name => /email|mail|upn|principal|mri|itemid|recipient|user|object|identity|entity|contact|member|person|account/i.test(name))
      .sort();
    probe.entity = entity;
    return {
      state: 'chip_verified',
      display_label: probe.selected.label,
      identity_evidence: 'picker_identity_matches_chip',
      identity_attribute_names: attributeNames
    };
  }
  if (phase === 'mention_probe_clear') {
    const probe = window.__officeMentionProbe;
    if (!probe || editors.length !== 1 || editors[0] !== probe.editor || !headerMatches())
      return {state: 'probe_changed'};
    if (probe.selected) {
      const entities = mentionEntities(editors[0]);
      if (entities.length !== 1 ||
          (probe.entity && !mentionEntityMatches(probe.entity, probe.selected)) ||
          (!probe.entity && normalizeText(entities[0].innerText || entities[0].textContent || '') !==
            normalizeText(probe.selected.label)) ||
          normalizeText(editors[0].innerText || '') !== normalizeText(probe.selected.label))
        return {state: 'probe_changed'};
    } else if (editors[0].innerHTML === probe.before) {
      delete window.__officeMentionProbe;
      return {state: 'composer_restored'};
    } else if (normalizeText(editors[0].innerText || '') !== probe.query ||
        mentionEntities(editors[0]).length)
      return {state: 'probe_changed'};
    probe.editor.focus();
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(probe.editor);
    selection.removeAllRanges();
    selection.addRange(range);
    return {state: document.activeElement === probe.editor ? 'probe_clear_ready' : 'probe_changed'};
  }
  if (phase === 'mention_probe_cleared') {
    const probe = window.__officeMentionProbe;
    if (!probe || editors.length !== 1 || editors[0] !== probe.editor || !headerMatches())
      return {state: 'probe_changed'};
    if (normalizeText(probe.editor.innerText) || mentionEntities(probe.editor).length)
      return {state: 'composer_not_restored'};
    delete window.__officeMentionProbe;
    return {state: 'composer_restored'};
  }
  if (phase === 'send_track_text') {
    const operation = window.__officeRequest;
    if (!operation || operation.id !== requestId || operation.clicked ||
        typeof request.trackText !== 'string' || editors.length !== 1 ||
        !headerMatches() ||
        normalizeText(editors[0].innerText || '') !== normalizeText(operation.expectedDraft))
      return {state: 'draft_changed'};
    operation.expectedDraft += request.trackText;
    editors[0].focus();
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(editors[0]);
    range.collapse(false);
    selection.removeAllRanges();
    selection.addRange(range);
    return {state: 'track_ready'};
  }
  if (phase === 'mention_candidate') {
    const operation = window.__officeRequest;
    if (!operation || operation.id !== requestId || operation.clicked ||
        !mention || typeof mention.key !== 'string' ||
        typeof mention.recipient_email !== 'string' ||
        typeof mention.display_label !== 'string' || editors.length !== 1 ||
        !headerMatches() ||
        normalizeText(editors[0].innerText || '') !== normalizeText(operation.expectedDraft))
      return {state: 'draft_changed'};
    const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[role="alertdialog"]'))
      .filter(visible);
    if (dialogs.some(dialog => /\b(join|add|invite)\b.{0,35}\b(chat|conversation|people)\b/i
      .test(normalizeText(dialog.innerText || dialog.textContent))))
      return {state: 'membership_prompt'};
    const matches = mentionCandidateMatches(mention.recipient_email, mention.display_label);
    const options = Array.from(document.querySelectorAll('[role="option"]')).filter(visible);
    if (!matches.length)
      return options.length ? {state: 'identity_unverified'} : {state: 'waiting'};
    if (matches.length !== 1) return {state: 'identity_ambiguous'};
    const query = '@' + (mention.query_text || mention.recipient_email);
    if (!operation.expectedDraft.toLowerCase().endsWith(query.toLowerCase()))
      return {state: 'draft_changed'};
    operation.candidate = {
      key: mention.key,
      email: mention.recipient_email.toLowerCase(),
      label: mention.display_label,
      identity: matches[0].identity,
      option: matches[0].option,
      query,
      prefix: operation.expectedDraft.slice(0, operation.expectedDraft.length - query.length)
    };
    return {state: 'candidate_verified'};
  }
  if (phase === 'mention_select') {
    const operation = window.__officeRequest;
    const candidate = operation?.candidate;
    if (!operation || operation.id !== requestId || operation.clicked || !candidate ||
        !mention || candidate.key !== mention.key || editors.length !== 1 ||
        !headerMatches() ||
        normalizeText(editors[0].innerText || '') !==
          normalizeText(candidate.prefix + candidate.query))
      return {state: 'draft_changed'};
    const matches = mentionCandidateMatches(candidate.email, candidate.label);
    if (matches.length !== 1 || matches[0].option !== candidate.option ||
        JSON.stringify(matches[0].identity) !== JSON.stringify(candidate.identity))
      return {state: 'candidate_changed'};
    const option = matches[0].option;
    if (option.getAttribute('aria-disabled') === 'true' || option.disabled)
      return {state: 'candidate_disabled'};
    option.click();
    operation.selected = candidate;
    return {state: 'candidate_selected'};
  }
  if (phase === 'mention_chip') {
    const operation = window.__officeRequest;
    const selected = operation?.selected;
    if (!operation || operation.id !== requestId || operation.clicked || !selected ||
        editors.length !== 1 || !headerMatches())
      return {state: 'draft_changed'};
    const matches = mentionEntities(editors[0])
      .filter(entity => mentionEntityMatches(entity, selected));
    if (!matches.length) return {state: 'waiting'};
    if (matches.length !== 1) return {state: 'chip_ambiguous'};
    operation.expectedDraft = selected.prefix + selected.label;
    if (normalizeText(editors[0].innerText || '') !== normalizeText(operation.expectedDraft))
      return {state: 'chip_label_mismatch'};
    operation.verifiedMentions = Array.isArray(operation.verifiedMentions)
      ? operation.verifiedMentions : [];
    operation.verifiedMentions.push({
      key: selected.key, email: selected.email, label: selected.label,
      identity: mentionIdentityAttributes(matches[0])
    });
    delete operation.candidate;
    delete operation.selected;
    return {state: 'mention_chip_verified'};
  }
  if (phase === 'send_abort_cleanup') {
    const operation = window.__officeRequest;
    if (!operation || operation.id !== requestId || operation.clicked ||
        editors.length !== 1 || !headerMatches() ||
        pendingComposerFiles(editors[0]) ||
        editors[0].querySelector('img,video,table,iframe,object,a') ||
        normalizeText(editors[0].innerText || '') !== normalizeText(operation.expectedDraft))
      return {state: 'composer_not_restored'};
    const entities = mentionEntities(editors[0]);
    const verified = operation.verifiedMentions || [];
    if (entities.length !== verified.length ||
        verified.some(item => entities.filter(entity =>
          mentionEntityMatches(entity, item)).length !== 1))
      return {state: 'composer_not_restored'};
    editors[0].focus();
    return {state: document.activeElement === editors[0]
      ? 'composer_clear_ready' : 'composer_not_restored'};
  }
  if (phase === 'send_abort_cleared') {
    const operation = window.__officeRequest;
    if (!operation || operation.id !== requestId || operation.clicked ||
        editors.length !== 1 || !headerMatches() ||
        normalizeText(editors[0].innerText) || mentionEntities(editors[0]).length ||
        pendingComposerFiles(editors[0]))
      return {state: 'composer_not_restored'};
    delete window.__officeRequest;
    return {state: 'composer_restored'};
  }
  if (phase === 'locate')
    return headerMatches() && matchingRows().length === 1 ? {state: 'ready'} : {state: 'navigate'};
  if (phase === 'navigate') {
    if (editors.some(hasDraft)) return {state: 'draft_exists'};
    if (openChatListOnce()) return {state: 'waiting'};
    const hits = matchingRows();
    if (hits.length !== 1) return {state: 'waiting'};
    hits[0].click();
    return {state: 'navigated'};
  }
  if (!headerMatches() || matchingRows().length !== 1)
    return {state: 'waiting', header_id_matches: header?.closest('[id^="chat-header-"]')?.id === 'chat-header-' + chatId,
      title_matches: header?.textContent.trim() === title, matching_rows: matchingRows().length};
  if (phase === 'upload_readiness') {
    if (editors.length !== 1)
      return {state: 'blocked', reason: 'identity_failed', composer_empty: false,
        file_input_available: false, file_input_count: uploadInputs().length, file_input_accepts: ''};
    const editor = editors[0];
    const inputs = uploadInputs();
    const composerEmpty = !hasDraftText(editor) && !pendingComposerFiles(editor);
    const controls = attachControls(composerRoot(editor));
    const available = inputs.length === 1 && !inputs[0].disabled &&
      (!inputs[0].files || inputs[0].files.length === 0);
    const attachAvailable = controls.length === 1;
    const accepts = inputs.length === 1 ? (inputs[0].getAttribute('accept') || '').slice(0, 160) : '';
    const reason = !composerEmpty ? 'composer_has_draft' :
      !available && !attachAvailable ? 'file_input_unavailable' : null;
    return {state: reason ? 'blocked' : 'ready', chat_id: chatId, title,
      composer_empty: composerEmpty, file_input_available: available,
      file_input_count: Math.min(inputs.length, 10), file_input_accepts: accepts,
      file_input_mode: available ? 'direct' : attachAvailable ? 'attach_menu' : 'unavailable',
      attach_control_available: attachAvailable, reason};
  }
  if (phase === 'upload_prepare') {
    if (!requestId || !/^[A-Za-z0-9._-]{1,128}$/.test(requestId) ||
        !Array.isArray(request.files) || request.files.length < 1 || request.files.length > 10 ||
        !request.files.every(file => file && typeof file.name === 'string' &&
          /^[^\\/:*?"<>|\x00-\x1f]{1,180}$/.test(file.name) &&
          !file.name.endsWith('.') && !file.name.endsWith(' ')) ||
        editors.length !== 1 || hasDraft(editors[0]))
      return {state: 'draft_exists'};
    if (window.__officeUploadRequest)
      return {state: 'upload_request_exists'};
    const inputs = uploadInputs();
    const controls = attachControls(composerRoot(editors[0]));
    const direct = inputs.length === 1 && !inputs[0].disabled &&
      (!inputs[0].files || inputs[0].files.length === 0);
    const mode = direct ? 'direct' : controls.length === 1 ? 'attach_menu' : 'unavailable';
    if (mode === 'unavailable')
      return {state: 'attachment_input_unavailable'};
    const names = request.files.map(file => file.name);
    if (new Set(names.map(name => name.toLowerCase())).size !== names.length)
      return {state: 'attachment_limit_exceeded'};
    window.__officeUploadRequest = {
      id: requestId, files: request.files, before: Array.from(
        document.querySelectorAll('[data-tid="chat-pane-message"]')
      ).map(element => element.id), clicked: false, mode,
      menuOpened: false, pickerClicked: false
    };
    return {state: 'upload_prepared', input_mode: mode};
  }
  if (phase === 'upload_open_menu') {
    const upload = window.__officeUploadRequest;
    const root = editors.length === 1 ? composerRoot(editors[0]) : null;
    if (!upload || upload.id !== requestId || upload.mode !== 'attach_menu' ||
        upload.clicked !== false || upload.menuOpened || !root || hasDraft(editors[0]))
      return {state: 'draft_changed'};
    const controls = attachControls(root);
    if (controls.length !== 1) return {state: 'attachment_input_unavailable'};
    upload.menuOpened = true;
    controls[0].click();
    return {state: 'upload_menu_opened'};
  }
  if (phase === 'upload_open_picker') {
    const upload = window.__officeUploadRequest;
    if (!upload || upload.id !== requestId || upload.mode !== 'attach_menu' ||
        upload.menuOpened !== true || upload.pickerClicked || upload.clicked !== false)
      return {state: 'draft_changed'};
    const items = Array.from(document.querySelectorAll(
      '[role="menuitem"],[role="option"],button'
    )).filter(element => visible(element) && !element.disabled &&
      element.getAttribute('aria-disabled') !== 'true' &&
      /^(upload from this device|upload from device|upload from computer|browse this device|browse my computer)$/i
        .test(normalizeText(element.getAttribute('aria-label') || element.innerText ||
          element.textContent)));
    if (items.length === 0) return {state: 'waiting'};
    if (items.length !== 1) return {state: 'attachment_input_unavailable'};
    upload.pickerClicked = true;
    items[0].click();
    return {state: 'upload_picker_opened'};
  }
  if (phase === 'upload_wait' || phase === 'upload_click') {
    const upload = window.__officeUploadRequest;
    if (!upload || upload.id !== requestId || upload.clicked !== false ||
        JSON.stringify(upload.files) !== JSON.stringify(request.files) ||
        editors.length !== 1 || hasDraftText(editors[0]))
      return {state: 'draft_changed'};
    if (!uploadReady(upload.files)) return {state: 'waiting'};
    if (phase === 'upload_wait') return {state: 'upload_ready'};
    const root = composerRoot(editors[0]);
    const buttons = Array.from(root.querySelectorAll(
      'button[data-tid="newMessageCommands-send"],button[data-tid="sendMessageCommands-send"]'
    )).filter(element => element.getClientRects().length && !element.disabled &&
      element.getAttribute('aria-disabled') !== 'true');
    if (buttons.length !== 1) return {state: 'waiting'};
    upload.clicked = true;
    buttons[0].click();
    return {state: 'upload_clicked'};
  }
  if (phase === 'upload_observe') {
    const upload = window.__officeUploadRequest;
    if (!upload || upload.id !== requestId || upload.clicked !== true ||
        JSON.stringify(upload.files) !== JSON.stringify(request.files) || !authorName)
      return {state: 'failed'};
    const messages = Array.from(document.querySelectorAll('[data-tid="chat-pane-message"]'))
      .filter(element => !upload.before.includes(element.id) &&
        /^message-body-[0-9]{13}$/.test(element.id) &&
        uploadNamesVisible(element.closest('[data-tid="chat-pane-item"]') || element, upload.files));
    if (messages.length !== 1) return {state: 'waiting'};
    const message = messages[0], messageId = message.id.slice('message-body-'.length);
    if (message.getAttribute('data-mid') !== messageId) return {state: 'waiting'};
    let parent = message;
    for (let index = 0; parent && index < 5; index++, parent = parent.parentElement) {
      const author = parent.querySelector('[data-tid="message-author-name"]');
      const stamp = parent.querySelector('time');
      if (author && stamp && author.textContent.trim() === authorName &&
          Date.parse(stamp.getAttribute('datetime')) === Number(messageId))
        return {state: 'uploaded', chat_id: chatId, title,
          message_id: message.id, time: stamp.getAttribute('datetime'),
          files: upload.files.map(file => file.name)};
    }
    return {state: 'waiting'};
  }
  if (phase.startsWith('message_')) {
    const message=document.getElementById(messageId);
    if (phase==='message_locate' && !message) {
      const first=document.querySelector('[data-tid="chat-pane-message"]');
      let scroll=first;
      while (scroll && scroll!==document.body) {
        if (scroll.scrollHeight>scroll.clientHeight+1 &&
            /^(auto|scroll)$/.test(getComputedStyle(scroll).overflowY)) break;
        scroll=scroll.parentElement;
      }
      if (!scroll || scroll===document.body) return {state:'message_not_loaded'};
      const key=chatId+'\0'+messageId;
      if (window.__officeMessageLocate!==key) {
        window.__officeMessageLocate=key;
        scroll.scrollTop=scroll.scrollHeight;
      } else scroll.scrollTop=Math.max(0,scroll.scrollTop-Math.max(100,scroll.clientHeight*0.7));
      return {state:'waiting'};
    }
    if (!/^message-body-[0-9]{13}$/.test(messageId||'') ||
        !message || message.getAttribute('data-tid')!=='chat-pane-message')
      return {state:'message_not_loaded'};
    const result={chat_id:chatId,title,message_id:messageId};
    if (phase==='message_locate') return {...result,state:'message_loaded'};
    if (phase==='message_link') return {...result,state:'linked',
      url:'https://teams.microsoft.com/l/message/'+encodeURIComponent(chatId)+'/'+
        messageId.slice('message-body-'.length)+'?context='+encodeURIComponent('{"contextType":"chat"}')};
    const contentText=()=> (message.querySelector('[data-message-content]') || message).innerText;
    if (phase!=='message_observe' && !request.readCurrent &&
        (typeof expectedText!=='string' ||
         (request.contentOnly?contentText():message.innerText)!==expectedText))
      return {state:'message_changed'};
    const root=message.closest('[data-tid="chat-pane-item"]');
    if (!root || root.querySelectorAll('[data-tid="chat-pane-message"]').length!==1)
      return {state:'message_ambiguous'};
    if (phase==='message_open') {
      const triggers=message.querySelectorAll('button[data-tid="message-actions-menu-hidden-button"]');
      if (triggers.length!==1 || triggers[0].disabled) return {state:'reaction_unavailable'};
      window.__officeMessageControls={chatId,title,messageId};
      triggers[0].focus({preventScroll:true});
      triggers[0].click();
      return {...result,state:'toolbar_opening'};
    }
    if (phase==='message_hover') {
      message.scrollIntoView({block:'center',behavior:'instant'});
      const r=message.getBoundingClientRect();
      return {...result,state:'hover',point:{x:r.x+r.width/2,y:r.y+Math.min(r.height/2,20)}};
    }
    const visible=e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
    const opened=window.__officeMessageControls;
    const bound=opened && opened.chatId===chatId && opened.title===title && opened.messageId===messageId;
    if (phase==='message_picker_open') {
      if (!bound) return {state:'reaction_unavailable'};
      const more=Array.from(document.querySelectorAll(
        '[role="toolbar"][aria-label="Reactions and message actions"] [data-tid="expanded-reactions-picker-entry"]'))
        .filter(visible);
      if (more.length!==1 || more[0].disabled) return {state:'reaction_unavailable'};
      window.__officeReactionPicker={chatId,title,messageId};
      more[0].click();
      return {...result,state:'picker_opening'};
    }
    const toolbars=Array.from((bound?document:root).querySelectorAll('[role="toolbar"][aria-label="Reactions and message actions"]'))
      .filter(visible);
    const labels=['Like','Heart','Laugh','Surprised','Sad','Angry'];
    const reactionName=e=>{
      const label=e.getAttribute('aria-label')||'';
      const tid=e.getAttribute('data-tid')||'';
      if (/^message-actions-yes(?:-tone[1-6])?$/.test(tid) && /^Like(?: with .+ skin tone)?$/.test(label))
        return 'Like';
      return labels.includes(label)?label:null;
    };
    const controls=toolbars.length===1?Array.from(toolbars[0].querySelectorAll('button'))
      .filter(e=>visible(e)&&reactionName(e)):[];
    const selected=e=>e.getAttribute('aria-pressed')==='true'||e.getAttribute('aria-checked')==='true';
    const itemId=e=>e.querySelector('img[itemid]')?.getAttribute('itemid') ||
      (e.getAttribute('data-tid')||'').replace(/^message-actions-/,'');
    const pills=id=>Array.from(message.querySelectorAll('[data-tid="diverse-reaction-pill-button"]'))
      .filter(e=>e.querySelector('img[itemid]')?.getAttribute('itemid')===id);
    const chosen=id=>pills(id).some(selected);
    const available=controls.filter(e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true');
    const pickerBinding=window.__officeReactionPicker;
    const pickerBound=bound && pickerBinding?.chatId===chatId && pickerBinding?.title===title &&
      pickerBinding?.messageId===messageId;
    const picker=pickerBound?document.getElementById('expanded-reaction-picker-popup-surface'):null;
    const emoji=picker?Array.from(picker.querySelectorAll('button[data-tid^="emoticon-button-"]'))
      .filter(e=>visible(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true' &&
        e.getAttribute('aria-label') && e.querySelector('img[itemid]')):[];
    const name=e=>{
      if (controls.includes(e)) return reactionName(e);
      const label=e.getAttribute('aria-label');
      return label.startsWith('Undo ') && chosen(itemId(e)) ? label.slice(5) : label;
    };
    const all=[...available,...emoji];
    if (phase==='message_reactions') {
      const reactions=all.map(e=>({name:name(e),selected:selected(e)||chosen(itemId(e)),emoji_id:itemId(e)}))
        .filter((e,i,a)=>a.findIndex(other=>other.name===e.name)===i);
      const eyes=pills('1f440_eyes');
      if (!reactions.some(e=>e.emoji_id==='1f440_eyes') && eyes.length===1 &&
          ['true','false'].includes(eyes[0].getAttribute('aria-pressed')))
        reactions.push({name:'Eyes',selected:selected(eyes[0]),emoji_id:'1f440_eyes'});
      return {...result,state:'reactions',content_text:contentText(),
        picker_ready:emoji.length>0,reactions};
    }
    if (phase==='message_observe') {
      const prior=window.__officeReaction;
      if (!prior || prior.id!==requestId || prior.chatId!==chatId || prior.title!==title ||
          prior.messageId!==messageId || prior.reaction!==request.reaction ||
          prior.content!==contentText()) return {state:'reaction_uncertain'};
      const pill=pills(prior.itemId);
      const control=available.find(e=>itemId(e)===prior.itemId);
      const isSelected=chosen(prior.itemId) || !!control && selected(control);
      // Removal needs a still-rendered content node and either an explicit false
      // control or a settled absence of this reaction's pill.
      const settled=prior.desired?isSelected:
        !isSelected && (!!message.querySelector('[data-message-content]') ||
          !!control && control.getAttribute('aria-pressed')==='false') &&
        pill.every(e=>e.getAttribute('aria-pressed')==='false');
      if (!settled) {prior.stableSince=0;return {state:'waiting'};}
      if (!prior.stableSince) {prior.stableSince=Date.now();return {state:'waiting'};}
      return Date.now()-prior.stableSince>=2000?
        {...result,state:prior.desired?'reacted':'removed',reaction:request.reaction}:{state:'waiting'};
    }
    if (phase==='message_set_reaction' && request.selected===false) {
      const own=pills(request.emojiId);
      if (own.length>1 || own.some(e=>!['true','false'].includes(e.getAttribute('aria-pressed'))))
        return {state:'reaction_uncertain'};
      if (!message.querySelector('[data-message-content]')) return {state:'reaction_unavailable'};
      if (!own.some(selected)) return {...result,state:'already_removed',reaction:request.reaction};
      if (window.__officeReaction?.id===requestId) return {state:'reaction_uncertain'};
      window.__officeReaction={id:requestId,chatId,title,messageId,reaction:request.reaction,
        itemId:request.emojiId,content:contentText(),desired:false};
      own[0].click();
      return {...result,state:'reaction_clicked'};
    }
    const matches=all.filter(e=>name(e)===request.reaction);
    if (matches.length!==1) return {state:'reaction_unavailable'};
    if (phase==='message_react' || phase==='message_set_reaction') {
      if (phase==='message_set_reaction' && itemId(matches[0])!==request.emojiId)
        return {state:'reaction_unavailable'};
      if (!emoji.includes(matches[0]) && !['true','false'].includes(matches[0].getAttribute('aria-pressed')||
          matches[0].getAttribute('aria-checked'))) return {state:'reaction_unavailable'};
      if (selected(matches[0])||chosen(itemId(matches[0])))
        return {...result,state:'already_reacted',reaction:request.reaction};
      const prior=window.__officeReaction;
      if (prior && prior.id===requestId) return {state:'reaction_uncertain'};
      window.__officeReaction={id:requestId,chatId,title,messageId,reaction:request.reaction,
        itemId:itemId(matches[0]),content:contentText(),desired:true};
      matches[0].click();
      return {...result,state:'reaction_clicked'};
    }
    return {state:'unsupported_phase'};
  }
  if (phase.startsWith('edit_')) {
    const ownMessage = () => {
      const message = document.getElementById(messageId);
      if (!message || !message.closest('.fui-ChatMyMessage') ||
          message.getAttribute('data-tid') !== 'chat-pane-message') return null;
      let root = message;
      for (let i=0; root && i<7; i++, root=root.parentElement) {
        const author = root.querySelector('[data-tid="message-author-name"]');
        if (author && author.textContent.trim() === authorName) return message;
      }
      return null;
    };
    const edit = window.__officeEditRequest;
    const bound = edit && edit.id === requestId && edit.chatId === chatId &&
        edit.title === title && edit.messageId === messageId && edit.expectedText === expectedText;
    const mainUnchanged = () => {
      const main = document.getElementById(edit.mainId);
      return !!main && !hasDraft(main);
    };
    const inlineEditor = () => {
      const found = editors.filter(e => e.id !== edit.mainId);
      return found.length === 1 ? found[0] : null;
    };
    const commands = (editor, tid, label) => {
      let root = editor;
      for (let i=0; root && i<16; i++, root=root.parentElement) {
        const matches = Array.from(root.querySelectorAll('button'))
            .filter(e => e.getAttribute('data-tid') === tid &&
                e.getAttribute('aria-label') === label && e.getClientRects().length &&
                !e.disabled && e.getAttribute('aria-disabled') !== 'true');
        if (matches.length === 1 && root.querySelectorAll('[data-tid="ckeditor"][contenteditable="true"]').length === 1)
          return matches[0];
      }
      return null;
    };
    if (phase === 'edit_begin') {
      if (edit && edit.id === requestId) return {state: 'failed'};
      const message = ownMessage();
      if (!message || message.innerText !== expectedText) return {state: 'message_changed'};
      if (editors.length !== 1 || !editors[0].id || hasDraft(editors[0])) return {state: 'draft_exists'};
      const triggers = message.querySelectorAll('button[data-tid="message-actions-menu-hidden-button"]');
      if (triggers.length !== 1 || triggers[0].disabled) return {state: 'edit_unavailable'};
      window.__officeEditRequest = {
        id: requestId, chatId, title, messageId, expectedText, mainId: editors[0].id,
        originalProjection: plainProjection(message),
        opened: false, pasted: false, clicked: false
      };
      message.scrollIntoView({block:'center', behavior:'instant'});
      triggers[0].focus({preventScroll:true});
      triggers[0].click();
      return {state: 'toolbar_opening'};
    }
    if (!bound) return {state: 'failed'};
    if (phase === 'edit_open') {
      const message = ownMessage();
      if (edit.opened || !message || plainProjection(message) !== edit.originalProjection || !mainUnchanged())
        return {state: 'message_changed'};
      const controls = Array.from(document.querySelectorAll(
          '[role="toolbar"][aria-label="Reactions and message actions"] button[data-tid="message-actions-edit"]'))
          .filter(e => e.getClientRects().length && !e.disabled && e.getAttribute('aria-disabled') !== 'true');
      if (controls.length !== 1) return {state: 'waiting'};
      edit.opened = true;
      controls[0].click();
      return {state: 'editor_opening'};
    }
    if (phase === 'edit_observe') {
      if (!edit.clicked || !mainUnchanged()) return {state: 'failed'};
      const message = ownMessage();
      if (!message || messageBeingEdited(message) || !renderedMatches(message, body, formatting) ||
          message.getAttribute('data-mid') !== messageId.slice('message-body-'.length))
        return {state: 'waiting'};
      let root = message;
      for (let i=0; root && i<7; i++, root=root.parentElement) {
        const time = root.querySelector('time');
        if (time && Date.parse(time.getAttribute('datetime')) === Number(messageId.slice('message-body-'.length)))
          return {state: 'edited', chat_id:chatId, title, message_id:messageId, time:time.getAttribute('datetime')};
      }
      return {state: 'waiting'};
    }
    const editor = inlineEditor();
    if (!edit.opened || edit.clicked || !mainUnchanged()) return {state:'failed'};
    if (!editor) return {state:'waiting'};
    if (phase === 'edit_prepare') {
      if (plainProjection(editor) !== edit.originalProjection ||
          !commands(editor, 'newMessageCommands-send', 'Done')) return {state:'editor_changed'};
      const mentions = Array.from(editor.querySelectorAll('mention'));
      const groups = Array.from(editor.querySelectorAll('readonly[itemtype="http://schema.skype.com/Mention"]'));
      if (mentions.length) {
        if (groups.length !== 1 || mentions.some(m => !groups[0].contains(m)))
          return {state:'unsupported_mentions'};
        const group = groups[0], block = editor.firstElementChild;
        if (!block || group.parentElement !== block ||
            Array.from(block.childNodes).slice(0, Array.from(block.childNodes).indexOf(group))
                .some(n => (n.textContent || '').trim())) return {state:'unsupported_mentions'};
        edit.mentionPrefix = group.textContent;
        edit.mentionHTML = group.outerHTML;
      } else {
        edit.mentionPrefix = '';
        edit.mentionHTML = '';
      }
      editor.focus({preventScroll:true});
      return {state: document.activeElement === editor ? 'editor_prepared' : 'failed'};
    }
    const editPayload = () => {
      const prepared = validatedFragment();
      if (!prepared || typeof edit.mentionPrefix !== 'string') return null;
      let payloadHTML = bodyFormat === 'markdown' ? prepared.template.innerHTML : null;
      let payloadText = body;
      let group = null;
      if (edit.mentionPrefix) {
        group = editor.querySelector('readonly[itemtype="http://schema.skype.com/Mention"]');
        if (!group || group.outerHTML !== edit.mentionHTML) return null;
        const escaped = edit.mentionPrefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
        const prefix = new RegExp('^' + escaped).exec(payloadText);
        if (!prefix) return null;
        payloadText = payloadText.slice(prefix[0].length);
        if (bodyFormat === 'markdown') {
          const first = prepared.template.content.firstElementChild;
          if (!first || first.tagName !== 'P') return null;
          const match = new RegExp('^' + escaped).exec(first.textContent);
          if (!match) return null;
          const walker = document.createTreeWalker(first, NodeFilter.SHOW_TEXT);
          let left=match[0].length, node, endpoint=null;
          while ((node=walker.nextNode())) {
            if (node.length >= left) {endpoint=node; break;}
            left -= node.length;
          }
          if (!endpoint) return null;
          const prefixRange = document.createRange();
          prefixRange.selectNodeContents(first);
          prefixRange.setEnd(endpoint, left);
          prefixRange.deleteContents();
          payloadHTML = prepared.template.innerHTML;
        }
      }
      return {payloadHTML, payloadText, group};
    };
    if (phase === 'edit_paste') {
      if (!editPayload()) return {state:'invalid_format'};
      if (edit.pasted) return renderedMatches(editor, body, formatting) ? {state:'edit_pasted'} : {state:'waiting'};
      if (plainProjection(editor) !== edit.originalProjection) return {state:'editor_changed'};
      const payload = editPayload();
      if (!payload) return {state:'mention_changed'};
      const instance = editor.ckeditorInstance;
      if (!instance || !instance.model || instance.isReadOnly ||
          !instance.data?.processor?.toView || !instance.data?.toModel ||
          !instance.editing?.view?.domConverter?.domRangeToView ||
          !instance.editing?.mapper?.toModelRange || !instance.model.insertContent)
        return {state:'editor_changed'};
      if (payload.group && payload.group.outerHTML !== edit.mentionHTML)
        return {state:'mention_changed'};
      const domRange = document.createRange();
      domRange.selectNodeContents(editor);
      if (payload.group) domRange.setStartAfter(payload.group);
      if (payload.group && domRange.intersectsNode(payload.group))
        return {state:'mention_changed'};
      let modelFragment, modelRange;
      try {
        let html = payload.payloadHTML;
        if (html === null) {
          const plain = document.createElement('div');
          plain.textContent = payload.payloadText;
          html = plain.innerHTML;
        }
        const viewFragment = instance.data.processor.toView(html);
        modelFragment = instance.data.toModel(viewFragment);
        const viewRange = instance.editing.view.domConverter.domRangeToView(domRange);
        modelRange = instance.editing.mapper.toModelRange(viewRange);
      } catch (_) {
        return {state:'editor_changed'};
      }
      try {
        instance.model.change(writer => {
          writer.setSelection(modelRange);
          instance.model.insertContent(modelFragment, instance.model.document.selection);
        });
      } catch (_) {
        return {state:'editor_changed'};
      }
      edit.pasted = true;
      return renderedMatches(editor, body, formatting) ? {state:'edit_pasted'} : {state:'waiting'};
    }
    if (phase === 'edit_save') {
      if (!edit.pasted || !validatedFragment() || !renderedMatches(editor, body, formatting))
        return {state:'editor_changed'};
      if (edit.mentionPrefix && editor.querySelector('readonly[itemtype="http://schema.skype.com/Mention"]')?.outerHTML !== edit.mentionHTML)
        return {state:'mention_changed'};
      const done = commands(editor, 'newMessageCommands-send', 'Done');
      if (!done) return {state:'save_unavailable'};
      edit.clicked = true;
      done.click();
      return {state:'edit_clicked'};
    }
    return {state:'failed'};
  }
  if (phase === 'read') {
    const messageNodes = Array.from(document.querySelectorAll('[data-tid="chat-pane-message"]'))
        .filter(e => e.getClientRects().length > 0);
    if (messageNodes.some(messageBeingEdited)) return {state: 'message_edit_in_progress'};
    const oldest = messageNodes[0] || null;
    const scrollAncestors = [], scrollElements = [];
    for (let node = oldest?.parentElement, depth = 0; node && depth < 12 && node !== document.body;
         node = node.parentElement, depth++) {
      const style = getComputedStyle(node);
      if (node.clientHeight > 0 && node.scrollHeight > node.clientHeight + 1 &&
          /^(auto|scroll)$/.test(style.overflowY)) {
        scrollElements.push(node);
        scrollAncestors.push({
          id: node.id || '', tid: node.getAttribute('data-tid') || '',
          role: node.getAttribute('role') || '', depth,
          scroll_top: node.scrollTop, client_height: node.clientHeight,
          scroll_height: node.scrollHeight, overflow_y: style.overflowY
        });
      }
    }
    const olderControls = Array.from(document.querySelectorAll('button,[role="button"]'))
        .filter(e => e.getClientRects().length > 0)
        .map(e => ({element: e, label: (e.getAttribute('aria-label') || e.getAttribute('title') ||
          e.innerText || '').trim().slice(0, 100)}))
        .filter(item => /older|earlier|previous|load more|more messages|history/i.test(item.label))
        .map(item => ({label: item.label,
          tid: item.element.getAttribute('data-tid') || '',
          disabled: !!item.element.disabled || item.element.getAttribute('aria-disabled') === 'true'}));
    const readLimit = Number(limit);
    const currentMessages = messageNodes.map(e => {
      let p = e, author = '', time = '';
      for (let i = 0; p && i < 5; i++, p = p.parentElement) {
        const a = p.querySelector('[data-tid="message-author-name"]');
        const t = p.querySelector('time');
        if (a && t) { author = a.textContent.trim(); time = t.getAttribute('datetime') || ''; break; }
      }
      return {id: e.id, text: e.innerText.slice(0, 2000), author, time};
    });
    const messageKey = message => message.id ||
        `${message.time}\u0000${message.author}\u0000${message.text}`;
    const requestKey = `${navigationId}\u0000${chatId}\u0000${title}\u0000${readLimit}`;
    let history = window.__officeReadHistory;
    if (!history || history.key !== requestKey) {
      history = {key: requestKey, messages: [], keys: new Set(), startedAt: Date.now(),
        pendingTop: false, pendingNudge: false, lastScrollAt: 0, scrollRequests: 0, scrollElement: null,
        initialScrollHeight: 0, initialClientHeight: 0, initialScrollTop: 0};
      window.__officeReadHistory = history;
    }
    const addMessages = () => {
      let firstShared = currentMessages.findIndex(message => history.keys.has(messageKey(message)));
      let added = 0;
      if (!history.messages.length) {
        for (const message of currentMessages.slice(-readLimit)) {
          const key = messageKey(message);
          if (!history.keys.has(key)) { history.messages.push(message); history.keys.add(key); added++; }
        }
      } else if (firstShared > 0 || (firstShared < 0 && history.pendingTop)) {
        const older = firstShared > 0 ? currentMessages.slice(0, firstShared) : currentMessages;
        const uniqueOlder = older.filter(message => !history.keys.has(messageKey(message)));
        for (const message of uniqueOlder) history.keys.add(messageKey(message));
        if (uniqueOlder.length) {
          history.messages = [...uniqueOlder, ...history.messages];
          added += uniqueOlder.length;
        }
      } else if (firstShared >= 0) {
        let lastShared = firstShared;
        for (let i = firstShared + 1; i < currentMessages.length; i++) {
          if (history.keys.has(messageKey(currentMessages[i]))) lastShared = i;
        }
        for (const message of currentMessages.slice(lastShared + 1)) {
          const key = messageKey(message);
          if (!history.keys.has(key)) { history.messages.push(message); history.keys.add(key); added++; }
        }
      }
      if (added) {
        history.messages = history.messages.slice(-readLimit);
        history.pendingTop = false;
        history.pendingNudge = false;
      }
      return added;
    };
    const added = addMessages();
    const finishRead = () => {
      const scroller = history.scrollElement;
      if (history.scrollRequests && scroller && scroller.isConnected) {
        const bottomGap = Math.max(0, history.initialScrollHeight -
            history.initialClientHeight - history.initialScrollTop);
        scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight - bottomGap);
      }
      const messages = history.messages.slice(-readLimit);
      window.__officeReadHistory = null;
      return {state: 'read', chat_id: chatId, title, messages,
        history_ui: {loaded_count: messageNodes.length, collected_count: history.messages.length,
          scroll_requests: history.scrollRequests,
          scroll_ancestors: scrollAncestors, older_controls: olderControls}};
    };
    if (history.messages.length >= readLimit || !messageNodes.length) return finishRead();

    const preferred = document.querySelectorAll('[data-tid="message-pane-list-viewport"]');
    const scrollElement = preferred.length === 1 && oldest && preferred[0].contains(oldest)
        ? preferred[0]
        : scrollElements.find(element => element.getAttribute('data-tid') === 'message-pane-list-viewport') ||
          (scrollElements.length === 1 ? scrollElements[0] : null);
    if (!scrollElement || history.scrollRequests >= 50 || Date.now() - history.startedAt > 12000)
      return finishRead();
    if (!history.scrollElement) {
      history.scrollElement = scrollElement;
      history.initialScrollHeight = scrollElement.scrollHeight;
      history.initialClientHeight = scrollElement.clientHeight;
      history.initialScrollTop = scrollElement.scrollTop;
    }
    if (added) history.pendingTop = false;
    if (history.pendingTop) {
      if (Date.now() - history.lastScrollAt < 1800) return {state: 'waiting'};
      return finishRead();
    }
    if (history.pendingNudge) {
      scrollElement.scrollTop = 0;
      history.pendingNudge = false;
      history.pendingTop = true;
      history.lastScrollAt = Date.now();
      history.scrollRequests++;
      return {state: 'waiting'};
    }
    if (scrollElement.scrollTop > 1) {
      scrollElement.scrollTop = 0;
      history.pendingTop = true;
      history.lastScrollAt = Date.now();
      history.scrollRequests++;
      return {state: 'waiting'};
    }
    if (scrollElement.scrollHeight <= scrollElement.clientHeight + 1) return finishRead();
    scrollElement.scrollTop = 1;
    history.pendingNudge = true;
    return {state: 'waiting'};
  }
  if (phase === 'inspect_message') {
    const message = document.getElementById(messageId);
    if (!message || message.getAttribute('data-tid') !== 'chat-pane-message')
      return {state: 'message_not_loaded'};
    if (messageBeingEdited(message)) return {state: 'message_edit_in_progress'};
    if (mentionDiagnostics === true) {
      const relevant = Array.from(
        message.querySelectorAll('mention,[itemtype="http://schema.skype.com/Mention"]')
      ).slice(0, 20);
      const hashSalt = Array.from(crypto.getRandomValues(new Uint32Array(4)),
        value => value.toString(16)).join('-');
      const safeAriaRoles = new Set([
        'alert', 'alertdialog', 'application', 'article', 'banner', 'blockquote',
        'button', 'caption', 'cell', 'checkbox', 'code', 'columnheader', 'combobox',
        'complementary', 'contentinfo', 'definition', 'deletion', 'dialog', 'directory',
        'document', 'emphasis', 'feed', 'figure', 'form', 'generic', 'grid', 'gridcell',
        'group', 'heading', 'img', 'insertion', 'link', 'list', 'listbox', 'listitem',
        'log', 'main', 'marquee', 'math', 'menu', 'menubar', 'menuitem',
        'menuitemcheckbox', 'menuitemradio', 'meter', 'navigation', 'none', 'note',
        'option', 'paragraph', 'presentation', 'progressbar', 'radio', 'radiogroup',
        'region', 'row', 'rowgroup', 'rowheader', 'scrollbar', 'search',
        'searchbox', 'separator', 'slider', 'spinbutton', 'status', 'strong', 'subscript',
        'superscript', 'switch', 'tab', 'table', 'tabpanel', 'term', 'textbox', 'time',
        'timer', 'toolbar', 'tooltip', 'tree', 'treegrid', 'treeitem'
      ]);
      const shape = value => {
        const text = String(value || '');
        const salted = hashSalt + '\0' + text;
        let hash = 2166136261;
        for (let i = 0; i < salted.length; i++)
          hash = Math.imul(hash ^ salted.charCodeAt(i), 16777619);
        let prefixClass = 'text';
        if (!text) prefixClass = 'empty';
        else if (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(text)) prefixClass = 'email';
        else if (/^[0-9a-f-]{16,}$/i.test(text)) prefixClass = 'hex-or-guid';
        else if (/^\d+$/.test(text)) prefixClass = 'numeric';
        else if (/^https?:\/\//i.test(text)) prefixClass = 'url';
        else if (/^[a-z0-9._-]+$/i.test(text)) prefixClass = 'identifier';
        return {prefix_class: prefixClass, length: Math.min(text.length, 4096),
          hash: (hash >>> 0).toString(16).padStart(8, '0')};
      };
      const elementEvidence = element => {
        const attributes = Array.from(element.attributes).slice(0, 30).map(attribute => ({
          name: attribute.name.slice(0, 80), value_shape: shape(attribute.value)
        }));
        const roleValue = element.getAttribute('role') || '';
        const itemtypeValue = element.getAttribute('itemtype') || '';
        const normalizedRole = roleValue.toLowerCase();
        const safeRole = safeAriaRoles.has(normalizedRole) ? normalizedRole : '';
        const safeItemtype =
          /^https?:\/\/schema\.skype\.com\/[a-z0-9._-]{1,40}$/i.test(itemtypeValue)
            ? itemtypeValue : '';
        const ancestors = [];
        let depth = 0;
        for (let parent = element.parentElement; parent && parent !== message;
          parent = parent.parentElement) {
          depth++;
          if (ancestors.length < 12) ancestors.unshift(parent.tagName.toLowerCase());
        }
        return {
          tag: element.tagName.toLowerCase(),
          depth: Math.min(depth, 19),
          ancestors,
          attribute_names: Array.from(element.attributes).slice(0, 30)
            .map(attribute => attribute.name.slice(0, 80)),
          attributes,
          role: safeRole,
          role_shape: shape(roleValue),
          itemtype: safeItemtype,
          data_tid_shape: shape(element.getAttribute('data-tid') || '')
        };
      };
      const evidence = relevant.map(elementEvidence);
      return {
        state: evidence.length ? 'inspected' : 'no_mentions',
        capture_date: new Date().toISOString().slice(0, 10),
        mentions: evidence
      };
    }
    const currentText = message.innerText;
    if (currentText.length > 2000) return {state: 'text_too_long'};
    const rich = {
      bold: Array.from(message.querySelectorAll('strong,b')).map(e => e.textContent).slice(0, 30),
      code: Array.from(message.querySelectorAll('code')).map(e => e.textContent).slice(0, 30),
      links: Array.from(message.querySelectorAll('a')).map(e => ({text:e.textContent, href:e.getAttribute('href')})).slice(0, 30),
      mentions: message.querySelectorAll('mention,[itemtype="http://schema.skype.com/Mention"]').length
    };
    let owner = '', time = '', root = message;
    for (let i = 0; root && i < 7; i++, root = root.parentElement) {
      const author = root.querySelector('[data-tid="message-author-name"]');
      const stamp = root.querySelector('time');
      if (author && stamp) {
        owner = author.textContent.trim();
        time = stamp.getAttribute('datetime') || '';
        break;
      }
    }
    const ownMessageMarker = !!message.closest('.fui-ChatMyMessage');
    const parents = [];
    let node = message;
    for (let i = 0; node && i < 6; i++, node = node.parentElement) {
      const controls = Array.from(node.querySelectorAll('button,[role="button"],[role="menuitem"]'))
          .slice(0, 20)
          .map(e => ({
            tag: e.tagName.toLowerCase(), role: e.getAttribute('role') || '',
            data_tid: e.getAttribute('data-tid') || '',
            aria_label: e.getAttribute('aria-label') || '', title: e.getAttribute('title') || '',
            aria_controls: e.getAttribute('aria-controls') || '',
            aria_expanded: e.getAttribute('aria-expanded') || '',
            aria_haspopup: e.getAttribute('aria-haspopup') || '',
            data_state: e.getAttribute('data-state') || '',
            text: (e.innerText || '').trim().slice(0, 80),
            disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true'
          }));
      parents.push({
        tag: node.tagName.toLowerCase(), role: node.getAttribute('role') || '',
        id: node.id || '', data_tid: node.getAttribute('data-tid') || '',
        aria_label: node.getAttribute('aria-label') || '',
        class_name: typeof node.className === 'string' ? node.className.slice(0, 160) : '',
        controls
      });
    }
    let menu = null, menuClosed = null;
    if (['open', 'read', 'verify_activate', 'activate'].includes(menuAction)) {
      if (!authorName || owner !== authorName || !ownMessageMarker) return {state: 'not_own_message'};
      const triggers = Array.from(message.querySelectorAll('button[data-tid="message-actions-menu-hidden-button"]'))
          .filter(e => e.getClientRects().length && !e.disabled && e.getAttribute('aria-disabled') !== 'true');
      if (triggers.length !== 1) return {state: 'edit_menu_trigger_unavailable'};
      const documentHasFocusBefore = document.hasFocus();
      if (menuAction === 'activate') {
        triggers[0].focus({preventScroll: true});
        triggers[0].click();
        return {state: 'menu_trigger_clicked'};
      }
      if (menuAction === 'open') {
        const rect = message.getBoundingClientRect();
        if (rect.bottom < 0 || rect.top > innerHeight || rect.right < 0 || rect.left > innerWidth) {
          message.scrollIntoView({block: 'center', behavior: 'instant'});
        }
        const r = message.getBoundingClientRect(), b = triggers[0].getBoundingClientRect();
        return {
          state: 'message_hover_target', document_has_focus: document.hasFocus(),
          point: {x:r.x + r.width / 2, y:r.y + Math.min(r.height / 2, 32)},
          trigger_point: {x:b.x + b.width / 2, y:b.y + b.height / 2},
          trigger_rect: {x:b.x,y:b.y,width:b.width,height:b.height}
        };
      }
      if (menuAction === 'verify_activate') {
        const r = triggers[0].getBoundingClientRect();
        const px = Number(request.pointerX), py = Number(request.pointerY);
        const hit = Number.isFinite(px) && Number.isFinite(py) ? document.elementFromPoint(px, py) : null;
        return {
          state: 'menu_trigger_ready', chat_id: chatId, title, message_id: message.id,
          own_message: !!authorName && owner === authorName && ownMessageMarker,
          point: {x:r.x + r.width / 2, y:r.y + r.height / 2},
          trigger_rect: {x:r.x,y:r.y,width:r.width,height:r.height},
          visible: !!r.width && !!r.height && getComputedStyle(triggers[0]).visibility === 'visible',
          hit_target: hit === triggers[0] || triggers[0].contains(hit)
        };
      }
      const visible = e => e.getClientRects().length && e.getAttribute('aria-hidden') !== 'true';
      const popupSelector = '[role="menu"],[role="toolbar"],[role="menubar"],[role="listbox"],[role="dialog"],[role="tooltip"],[data-tid*="menu"],[data-tid*="popover"]';
      const menus = Array.from(document.querySelectorAll('[role="menu"]')).filter(visible).slice(0, 12);
      const popupCandidates = Array.from(document.querySelectorAll(popupSelector))
          .filter(visible).filter(e => { const r=e.getBoundingClientRect(); return r.bottom >= 0 && r.top <= innerHeight && r.right >= 0 && r.left <= innerWidth; })
          .slice(0, 30).map(e => ({
            id: e.id || '', tag: e.tagName.toLowerCase(), role: e.getAttribute('role') || '',
            data_tid: e.getAttribute('data-tid') || '', aria_label: e.getAttribute('aria-label') || '',
            aria_controls: e.getAttribute('aria-controls') || '', aria_expanded: e.getAttribute('aria-expanded') || '',
            visible: !!visible(e), text: (e.innerText || '').trim().slice(0, 160),
            rect: (() => { const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; })(),
            buttons: Array.from(e.querySelectorAll('button,[role="button"],[role="menuitem"]')).slice(0, 12).map(b => ({
              role:b.getAttribute('role')||'', label:(b.getAttribute('aria-label')||b.innerText||'').trim().slice(0,80),
              data_tid:b.getAttribute('data-tid')||'', disabled:b.getAttribute('aria-disabled')==='true'
            }))
          }));
      menu = menus.map(e => ({
        id: e.id || '', aria_label: e.getAttribute('aria-label') || '',
        visible: !!visible(e),
        parent_id: e.parentElement?.id || '', parent_role: e.parentElement?.getAttribute('role') || '',
        items: Array.from(e.querySelectorAll('[role="menuitem"]')).slice(0, 20).map(item => ({
          label: (item.getAttribute('aria-label') || item.innerText || '').trim().slice(0, 80),
          data_tid: item.getAttribute('data-tid') || '',
          disabled: item.getAttribute('aria-disabled') === 'true'
        }))
      }));
      const triggerControls = triggers[0].getAttribute('aria-controls') || '';
      let relatedMenus = menus.filter(e => {
        const controlled = triggerControls.split(/\s+/).filter(Boolean).some(id => {
          const wrapper = document.getElementById(id);
          return wrapper && (wrapper === e || wrapper.contains(e) || e.contains(wrapper));
        });
        return controlled;
      });
      if (!relatedMenus.length && triggers[0].getAttribute('aria-expanded') === 'true' && menus.length === 1)
        relatedMenus = menus;
      if (relatedMenus.length === 1 && visible(relatedMenus[0])) {
        triggers[0].click();
        menuClosed = !visible(relatedMenus[0]) && triggers[0].getAttribute('aria-expanded') !== 'true';
      } else {
        menuClosed = !relatedMenus.some(visible) && triggers[0].getAttribute('aria-expanded') !== 'true';
      }
      return {
        state: 'inspected', chat_id: chatId, title, message_id: message.id,
        author: owner, own_message: !!authorName && owner === authorName && ownMessageMarker, text: currentText,
        time, parents, menu, menu_closed: menuClosed, popup_candidates: popupCandidates,
        trigger: {
          aria_label: triggers[0].getAttribute('aria-label') || '',
          aria_controls: triggers[0].getAttribute('aria-controls') || '',
          aria_expanded: triggers[0].getAttribute('aria-expanded') || '',
          aria_haspopup: triggers[0].getAttribute('aria-haspopup') || '',
          data_state: triggers[0].getAttribute('data-state') || '',
          focused: document.activeElement === triggers[0], document_has_focus: document.hasFocus(),
          document_had_focus_before: documentHasFocusBefore,
          data_tid: triggers[0].getAttribute('data-tid') || '',
          visibility: getComputedStyle(triggers[0]).visibility,
          opacity: getComputedStyle(triggers[0]).opacity,
          rect: (() => { const r=triggers[0].getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; })(),
          parent: {
            tag: triggers[0].parentElement?.tagName.toLowerCase() || '',
            id: triggers[0].parentElement?.id || '',
            role: triggers[0].parentElement?.getAttribute('role') || '',
            data_tid: triggers[0].parentElement?.getAttribute('data-tid') || '',
            aria_label: triggers[0].parentElement?.getAttribute('aria-label') || ''
          }
        }
      };
    }
    return {
      state: 'inspected', chat_id: chatId, title, message_id: message.id,
      author: owner, own_message: !!authorName && owner === authorName && ownMessageMarker, text: currentText, rich,
      time, parents, menu, menu_closed: menuClosed
    };
  }
  if (phase === 'prepare') {
    if (editors.length !== 1) return {state: 'waiting'};
    if (hasDraft(editors[0])) return {state: 'draft_exists'};
    window.__officeRequest = {
      id: requestId,
      before: Array.from(document.querySelectorAll('[data-tid="chat-pane-message"]')).map(e => e.id),
      clicked: false, pasted: false, bodyFormat, expectedDraft: '',
      verifiedMentions: []
    };
    editors[0].focus();
    return {state: document.activeElement === editors[0] ? 'prepared' : 'failed'};
  }
  if (phase === 'paste') {
    const prepared = validatedFragment();
    if (!prepared || editors.length !== 1 || window.__officeRequest?.id !== requestId ||
        window.__officeRequest.clicked !== false)
      return {state: 'failed'};
    if (window.__officeRequest.pasted === true)
      return renderedMatches(editors[0], body, formatting) ? {state: 'pasted'} : {state: 'waiting'};
    if (editors[0].innerText.trim() !== '') return {state: 'failed'};
    editors[0].focus();
    const range = document.createRange();
    range.selectNodeContents(editors[0]);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    const data = new DataTransfer();
    data.setData('text/plain', body);
    if (bodyFormat === 'markdown') data.setData('text/html', prepared.template.innerHTML);
    const event = new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true});
    editors[0].dispatchEvent(event);
    window.__officeRequest.pasted = true;
    return renderedMatches(editors[0], body, formatting) ? {state: 'pasted'} : {state: 'waiting'};
  }
  if (phase === 'send') {
    const prepared = validatedFragment();
    const verifiedMentions = window.__officeRequest?.verifiedMentions || [];
    const currentMentions = editors.length === 1 ? mentionEntities(editors[0]) : [];
    if (!prepared || editors.length !== 1 ||
        pendingComposerFiles(editors[0]) ||
        !renderedMatches(editors[0], visibleBody || body, formatting) ||
        window.__officeRequest?.id !== requestId || window.__officeRequest.clicked !== false ||
        !Array.isArray(window.__officeRequest.before) ||
        (Array.isArray(mentions) && mentions.length > 0 &&
          (normalizeText(window.__officeRequest.expectedDraft || '') !==
            normalizeText(visibleBody || body) ||
           currentMentions.length !== verifiedMentions.length ||
           verifiedMentions.some((expected, index) =>
             !currentMentions[index] || !mentionEntityMatches(currentMentions[index], expected)))) ||
        (Array.isArray(mentions) &&
          (mentions.length !== (window.__officeRequest.verifiedMentions || []).length ||
           mentions.some((item, index) => {
             const verified = window.__officeRequest.verifiedMentions[index];
             return !verified || verified.key !== item.key ||
               verified.email.toLowerCase() !== item.recipient_email.toLowerCase() ||
               verified.label !== item.display_label;
           }))) ||
        (bodyFormat === 'markdown' && window.__officeRequest.pasted !== true)) return {state: 'failed'};
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
        .filter(e => !window.__officeRequest.before.includes(e.id) &&
            renderedMatches(e, visibleBody || body, formatting) &&
            /^message-body-[0-9]{13}$/.test(e.id));
    if (matches.length !== 1) return {state: 'waiting'};
    const e = matches[0], mid = e.id.slice('message-body-'.length);
    if (e.getAttribute('data-mid') !== mid) return {state: 'waiting'};
    let p = e;
    for (let i = 0; p && i < 5; i++, p = p.parentElement) {
      const author = p.querySelector('[data-tid="message-author-name"]');
      const stamp = p.querySelector('time');
      if (author && stamp && author.textContent.trim() === authorName &&
          Date.parse(stamp.getAttribute('datetime')) === Number(mid)) {
        const observed = {
          state: 'observed', message_id: e.id, time: stamp.getAttribute('datetime'),
          chat_id: chatId, title
        };
        if (Array.isArray(mentions) && mentions.length) {
          const settled = mentionEntities(e);
          const receipts = [];
          for (const [index, expected] of (window.__officeRequest.verifiedMentions || []).entries()) {
            // Posted Teams entities replace the MRI with a message-local ordinal.
            // Tie that ordinal and label to the exact MRI-verified insertion plan
            // retained across the one click, in this new own settled message.
            const matchesPosition = entity => entity === settled[index] &&
              entity.getAttribute('itemtype') === 'http://schema.skype.com/Mention' &&
              entity.getAttribute('data-mention-type') === 'person' &&
              entity.getAttribute('itemid') === String(index) &&
              normalizeText(entity.innerText || entity.textContent) === normalizeText(expected.label);
            const candidates = settled.filter(entity =>
              mentionEntityMatches(entity, expected) || matchesPosition(entity));
            if (candidates.length !== 1) return {state: 'waiting'};
            receipts.push({
              key: expected.key,
              text: expected.label,
              recipient_email: expected.email,
              identity_evidence: mentionEntityMatches(candidates[0], expected)
                ? 'picker_identity_matches_settled_entity'
                : 'verified_picker_chip_and_settled_ordinal',
              entity_verified: true
            });
          }
          if (settled.length !== receipts.length) return {state: 'waiting'};
          observed.mentions = receipts;
        }
        return observed;
      }
    }
    return {state: 'waiting'};
  }
  return {state: 'failed'};
}
