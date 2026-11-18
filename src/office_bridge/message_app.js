// Both interactive hosts use this controller. The plain HTML fallback contains no script.
(() => {
  const isolated =
    parent === window &&
    location.origin === 'https://office-bridge.invalid' &&
    window.chrome?.webview;
  if (parent === window && !isolated) return;
  const post = (message) =>
    isolated ? isolated.postMessage(message) : parent.postMessage(message, '*');
  const notify = (method, params) => post({ jsonrpc: '2.0', method, params });
  const main = document.getElementById('messages');
  const actions = document.getElementById('window-action');
  const status = document.getElementById('action-status');
  let supportsTools = false,
    current,
    editor,
    pending,
    deliveredView,
    sequence = 1;
  const readTool = 'outlook_get_message',
    draftTool = 'outlook_create_reply_draft',
    windowTool = 'office_open_message_view';
  const failures = new Map([
    ['draft_exists', 'Outlook has an open draft. Close or save it before reading another email.'],
    ['message_not_found', 'This email is no longer in the loaded inbox. Refresh the inbox and select it again.'],
    ['message_identity_unavailable', 'Outlook has not supplied a verified identity for this email. Try Read email again.'],
    ['message_identity_ambiguous', 'Outlook opened more than one matching message. Select one email and retry the read.'],
    ['stale_message', 'This email changed. Read it again before creating a reply draft.'],
    ['version_incomplete', 'Outlook has not supplied a complete message version. Read the email again.'],
    ['mutation_outcome_unknown', 'Outlook did not confirm the change. Inspect Drafts before trying another write.'],
    ['not_ready', 'Outlook is still loading the selected email. Try Read email again.'],
    ['session_timeout', 'Outlook did not finish this operation within its time limit.']
  ]);
  function failureText(value) {
    const text = typeof value === 'string' ? value : JSON.stringify(value || '');
    for (const [code, message] of failures)
      if (new RegExp(`(^|[^a-z_])${code}($|[^a-z_])`, 'i').test(text))
        return `Office action failed. ${message}`;
    return 'Office action failed. The host did not confirm the operation.';
  }
  // Import the renderer's semantic markup only. A host result cannot add scripts,
  // URLs, styles, handlers or form submissions to the app's host bridge.
  const tags = new Set(
    'HEADER DIV P H1 H2 I SPAN SECTION ARTICLE LABEL INPUT DETAILS SUMMARY DL DT DD'.split(
      ' '
    )
  );
  function copyMarkup(node) {
    if (node.nodeType === Node.TEXT_NODE)
      return document.createTextNode(node.data);
    if (!tags.has(node.tagName)) return document.createDocumentFragment();
    const copy = document.createElement(node.localName);
    copy.className = node.className;
    for (const attr of ['aria-label', 'aria-hidden', 'title', 'data-unread']) {
      if (node.hasAttribute(attr))
        copy.setAttribute(attr, node.getAttribute(attr));
    }
    if (node.tagName === 'INPUT') copy.type = 'checkbox';
    if (node.tagName === 'DETAILS' && node.hasAttribute('name'))
      copy.name = 'inbox';
    copy.append(...Array.from(node.childNodes, copyMarkup));
    return copy;
  }
  function button(label, click) {
    const el = document.createElement('button');
    el.type = 'button';
    el.textContent = label;
    el.onclick = click;
    return el;
  }
  function call(name, args) {
    if (
      !supportsTools ||
      pending ||
      ![readTool, draftTool, windowTool].includes(name)
    )
      return Promise.reject(new Error('Action unavailable.'));
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      const finish = (error, result) => {
        clearTimeout(timer);
        pending = undefined;
        if (error) reject(error);
        else resolve(result);
      };
      const timer = setTimeout(
        () => finish(new Error(name === readTool
          ? 'Reading this email timed out. Refresh the inbox and try Read email again.'
          : 'The host timed out.')),
        name === readTool ? 40000 : 90000
      );
      pending = { id, finish };
      try {
        post({
          jsonrpc: '2.0',
          id,
          method: 'tools/call',
          params: { name, arguments: args }
        });
      } catch {
        finish(new Error('The host connection closed.'));
      }
    });
  }
  function valueOf(params) {
    const text = params?.content?.find((block) => block.type === 'text')?.text;
    let value = params?.structuredContent;
    if (!value && text) {
      try {
        value = JSON.parse(text);
      } catch {
        /* Plain MCP error text. */
      }
    }
    if (params?.isError || value?.error)
      throw new Error(failureText(value?.error || text));
    return value;
  }
  function notice(title, detail) {
    current = undefined;
    actions.replaceChildren();
    status.textContent = '';
    const heading = document.createElement('h1');
    const body = document.createElement('p');
    heading.textContent = title;
    body.textContent = detail;
    main.replaceChildren(heading, body);
  }
  const identifier = (value) =>
    typeof value === 'string' && value.trim().length > 0;
  const exact = (ref) => identifier(ref?.store_id) && identifier(ref.entry_id);
  const readable = (ref) =>
    exact(ref) ||
    (identifier(ref?.store_id) && identifier(ref.conversation_id));
  function resolves(request, result) {
    if (
      !readable(request) ||
      !exact(result) ||
      request.store_id !== result.store_id
    )
      return false;
    if (
      identifier(request.conversation_id) &&
      request.conversation_id !== result.conversation_id
    )
      return false;
    return (
      !identifier(request.entry_id) || request.entry_id === result.entry_id
    );
  }
  function replyable(binding) {
    if (!exact(binding?.source) || !binding.version) return false;
    const version = binding.version;
    if (
      'content_complete' in version ||
      'version_digest' in version ||
      identifier(binding.source.conversation_id)
    )
      return (
        version.content_complete === true &&
        /^[0-9a-f]{64}$/.test(version.version_digest)
      );
    return identifier(version.modified_at);
  }
  const sameFields = (left, right) =>
    left &&
    right &&
    Object.keys(left).length === Object.keys(right).length &&
    Object.keys(left).every((key) => left[key] === right[key]);
  const sameRef = (left, right) =>
    exact(left) && exact(right) && sameFields(left, right);
  function disableActions(disabled) {
    for (const el of document.querySelectorAll(
      '.mail-actions button, #window-action button'
    ))
      el.disabled = disabled;
  }
  async function read(ref) {
    if (pending || editor) return;
    disableActions(true);
    status.textContent = 'Reading email…';
    try {
      const value = valueOf(await call(readTool, { message: ref }));
      const binding = value?.presentation?.mail;
      if (
        !resolves(ref, value?.result?.ref) ||
        (binding &&
          (!sameRef(binding.source, value.result.ref) ||
            !sameFields(binding.version, value.result.version))) ||
        !show(value.presentation)
      )
        throw new Error('Outlook did not return the selected email.');
      main.querySelector('h1')?.focus();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      disableActions(false);
    }
  }
  function compose(binding, replyAll, trigger) {
    if (editor || pending) return;
    disableActions(true);
    const panel = document.createElement('div');
    panel.className = 'composer';
    const label = document.createElement('label');
    label.htmlFor = 'reply-text';
    label.textContent = replyAll ? 'Reply all' : 'Reply';
    const text = document.createElement('textarea');
    text.id = label.htmlFor;
    text.rows = 5;
    text.maxLength = 20000;
    const bar = document.createElement('div');
    bar.className = 'composer-actions';
    let attempted = false;
    const cancel = button('Cancel', () => {
      if (attempted || pending) return;
      editor = undefined;
      panel.remove();
      disableActions(false);
      status.textContent = '';
      trigger.focus();
    });
    const create = button('Create draft', async () => {
      if (attempted || pending || !text.value.trim()) return;
      attempted = true;
      create.disabled = cancel.disabled = text.readOnly = true;
      status.textContent = 'Creating draft…';
      try {
        const value = valueOf(
          await call(draftTool, {
            source: binding.source,
            expected_source: binding.version,
            body_text: text.value,
            reply_all: replyAll
          })
        );
        const draft = value?.result;
        if (
          !sameRef(draft?.source, binding.source) ||
          !exact(draft.ref) ||
          !draft.digest
        )
          throw new Error('Outlook did not confirm the draft.');
        editor = undefined;
        if (!show(value.presentation))
          notice(
            'Reply draft created',
            [draft.subject, draft.body_text].filter(Boolean).join('\n')
          );
        main.querySelector('h1')?.focus();
      } catch (error) {
        text.readOnly = false;
        status.textContent = `${error.message} Check Outlook Drafts before starting another reply. Your text is available here.`;
      }
    });
    create.className = 'primary-action';
    create.disabled = true;
    text.oninput = () => {
      create.disabled = attempted || !text.value.trim();
    };
    bar.append(create, cancel);
    panel.append(label, text, bar);
    trigger.closest('article').append(panel);
    editor = panel;
    status.textContent = '';
    text.focus();
  }
  function bind() {
    actions.replaceChildren();
    if (!current || !supportsTools) return;
    const { view_id, view, mail, window: windowLabel } = current;
    const cards = [...main.querySelectorAll('.rows > article')];
    if (view.source === 'Outlook' && cards.length === view.cards.length) {
      cards.forEach((card, index) => {
        if (view.cards[index].kind !== 'mail') return;
        const bar = document.createElement('div');
        bar.className = 'mail-actions';
        if (mail?.rows?.length === cards.length && readable(mail.rows[index])) {
          bar.append(button('Read email', () => read(mail.rows[index])));
        } else if (cards.length === 1 && replyable(mail)) {
          for (const [name, all] of [
            ['Reply', false],
            ['Reply all', true]
          ]) {
            const reply = button(name, () => compose(mail, all, reply));
            bar.append(reply);
          }
        }
        if (bar.childElementCount) card.append(bar);
      });
    }
    if (!isolated && windowLabel)
      actions.append(
        button('Open in window', async () => {
          if (pending || editor) return;
          disableActions(true);
          try {
            const args = { window: windowLabel, view_id };
            const receipt = valueOf(await call(windowTool, args));
            if (!receipt?.opened || receipt.view_id !== view_id)
              throw new Error('Window opening was not confirmed.');
            status.textContent = 'Opened in the Office Bridge window.';
          } catch (error) {
            status.textContent = error.message;
          } finally {
            disableActions(false);
          }
        })
      );
  }
  function show(display) {
    if (
      !display?.html ||
      !display.view_id ||
      !Array.isArray(display.view?.cards)
    )
      return false;
    const template = document.createElement('template');
    template.innerHTML = display.html;
    const content = template.content.querySelector('main');
    if (!content?.children.length) return false;
    current = display;
    main.replaceChildren(...Array.from(content.childNodes, copyMarkup));
    const heading = main.querySelector('h1');
    if (heading) heading.tabIndex = -1;
    status.textContent = '';
    bind();
    if (isolated) document.documentElement.dataset.officeReady = 'true';
    return true;
  }
  function accept(params) {
    if (editor || pending) return;
    let value;
    try {
      value = valueOf(params);
    } catch (error) {
      return notice('Office action needs attention', error.message);
    }
    if (value?.opened && value?.view_id) return;
    const display = value?.presentation || value;
    if (display?.view_id && display.view_id === deliveredView) return;
    if (show(display)) {
      deliveredView = display.view_id;
      return;
    }
    const result = value?.result || value;
    const outcome = new Map([
      ['submitted', 'Submitted to Outlook'],
      ['already_submitted', 'Previously submitted to Outlook'],
      ['observed', 'Observed in Teams'],
      ['already_observed', 'Previously observed in Teams']
    ]).get(result?.status);
    const context = [
      result?.subject,
      result?.chat?.title,
      result?.submitted_at || result?.time,
      result?.body_text,
      result?.body_truncated && 'Body truncated by Outlook'
    ];
    notice(
      outcome || 'Office result',
      (outcome ? context : [result?.status, result?.error])
        .filter(Boolean)
        .join('\n') || 'See the original action result.'
    );
  }
  function theme(context) {
    if (['light', 'dark'].includes(context?.theme))
      document.documentElement.style.colorScheme = context.theme;
  }
  function receive(data) {
    if (data?.jsonrpc !== '2.0') return;
    if (data.id === 1 && data.result && !initialized) {
      initialized = true;
      supportsTools = !!data.result.hostCapabilities?.serverTools;
      theme(data.result.hostContext);
      notify('ui/notifications/initialized');
      if (!isolated)
        new ResizeObserver(() =>
          notify('ui/notifications/size-changed', {
            height: Math.ceil(document.body.getBoundingClientRect().height)
          })
        ).observe(document.body);
    } else if (pending && data.id === pending.id) {
      pending.finish(
        data.error
          ? new Error(failureText(data.error?.message))
          : null,
        data.result
      );
    } else if (data.method === 'ui/notifications/tool-result')
      accept(data.params);
    else if (data.method === 'ui/notifications/host-context-changed')
      theme(data.params);
    else if (data.method === 'ui/resource-teardown')
      post({ jsonrpc: '2.0', id: data.id, result: {} });
  }
  let initialized = false;
  if (isolated)
    isolated.addEventListener('message', (event) => receive(event.data));
  else
    window.addEventListener('message', (event) => {
      if (event.source === parent) receive(event.data);
    });
  post({
    jsonrpc: '2.0',
    id: 1,
    method: 'ui/initialize',
    params: {
      protocolVersion: '2026-01-26',
      appInfo: { name: 'Office Bridge', version: '1.0.0' },
      appCapabilities: {}
    }
  });
})();
