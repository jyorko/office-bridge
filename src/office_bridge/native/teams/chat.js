(request, renderNodes) => {
  const {phase, chatId, title, limit, name, requestId, body, bodyFormat, nodes, formatting,
    authorName, navigationId, messageId, menuAction, expectedText} = request;
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
  const normalizeText = value => (value || '').replace(/\s+/g, ' ').trim();
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
      path: mix(location.pathname),
      chatHeader: header ? mix(header.textContent.trim()) : '',
      headerId: mix(header?.closest('[id^="chat-header-"]')?.id || ''),
      editors: editors.map(editor => ({
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
    return {state: 'snapshot', fingerprint: JSON.stringify(signature),
      editor_count: editors.length, dirty: editors.some(hasDraft)};
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
    return {state: 'waiting'};
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
    if (phase!=='message_observe' &&
        (typeof expectedText!=='string' || message.innerText!==expectedText))
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
    if (phase==='message_reactions') return {...result,state:'reactions',picker_ready:emoji.length>0,
      reactions:all.map(e=>({name:name(e),selected:selected(e)||chosen(itemId(e))}))
        .filter((e,i,a)=>a.findIndex(other=>other.name===e.name)===i)};
    if (phase==='message_observe') {
      const prior=window.__officeReaction;
      if (!prior || prior.id!==requestId || prior.chatId!==chatId || prior.title!==title ||
          prior.messageId!==messageId || prior.reaction!==request.reaction ||
          prior.content!==contentText()) return {state:'reaction_uncertain'};
      const settled=chosen(prior.itemId) ||
        available.some(e=>name(e)===prior.reaction && selected(e));
      if (!settled) {prior.stableSince=0;return {state:'waiting'};}
      if (!prior.stableSince) {prior.stableSince=Date.now();return {state:'waiting'};}
      return Date.now()-prior.stableSince>=2000?
        {...result,state:'reacted',reaction:request.reaction}:{state:'waiting'};
    }
    const matches=all.filter(e=>name(e)===request.reaction);
    if (matches.length!==1) return {state:'reaction_unavailable'};
    if (phase==='message_react') {
      if (!emoji.includes(matches[0]) && !['true','false'].includes(matches[0].getAttribute('aria-pressed')||
          matches[0].getAttribute('aria-checked'))) return {state:'reaction_unavailable'};
      if (selected(matches[0])||chosen(itemId(matches[0])))
        return {...result,state:'already_reacted',reaction:request.reaction};
      const prior=window.__officeReaction;
      if (prior && prior.id===requestId) return {state:'reaction_uncertain'};
      window.__officeReaction={id:requestId,chatId,title,messageId,reaction:request.reaction,
        itemId:itemId(matches[0]),content:contentText()};
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
      if (!message || !renderedMatches(message, body, formatting) ||
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
      clicked: false, pasted: false, bodyFormat
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
    if (!prepared || editors.length !== 1 || !renderedMatches(editors[0], body, formatting) ||
        window.__officeRequest?.id !== requestId || window.__officeRequest.clicked !== false ||
        !Array.isArray(window.__officeRequest.before) ||
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
            renderedMatches(e, body, formatting) &&
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
