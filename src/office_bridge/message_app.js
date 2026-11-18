// Host transport only. T3 and the isolated viewer use the script-free document directly.
(() => {
  if (parent === window) return;
  const main = document.getElementById('messages');
  const actions = document.getElementById('window-action');
  const notify = (method, params) =>
    parent.postMessage({ jsonrpc: '2.0', method, params }, '*');
  let supportsTools = false,
    current,
    pending,
    seq = 1;

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
  function notice(title, detail) {
    current = undefined;
    actions.replaceChildren();
    const heading = document.createElement('h1');
    const body = document.createElement('p');
    heading.textContent = title;
    body.textContent = detail;
    main.replaceChildren(heading, body);
  }
  function showOpenButton() {
    actions.replaceChildren();
    if (!supportsTools || !current?.view_id || !current.view) return;
    const button = document.createElement('button');
    button.textContent = 'Open in window';
    button.onclick = () => {
      const id = ++seq;
      button.disabled = true;
      button.textContent = 'Opening…';
      function finish(ok) {
        clearTimeout(timer);
        button.disabled = false;
        button.textContent = ok ? 'Open in window' : 'Try opening again';
        pending = undefined;
      }
      const timer = setTimeout(() => finish(false), 45000);
      pending = { id, finish };
      parent.postMessage(
        {
          jsonrpc: '2.0',
          id,
          method: 'tools/call',
          params: {
            name: 'office_open_message_view',
            arguments: { view_id: current.view_id, view: current.view }
          }
        },
        '*'
      );
    };
    actions.append(button);
  }
  function accept(params) {
    let value = params?.structuredContent;
    const text = params?.content?.find((block) => block.type === 'text')?.text;
    if (!value) {
      try {
        value = JSON.parse(text);
      } catch {
        return notice(params?.isError ? 'Office action needs attention' : 'Office result', text || 'No result was returned.');
      }
    }
    if (value?.opened && value?.view_id) return;
    if (pending) pending.finish(false);
    if (params.isError || value?.error)
      return notice(
        'Office action needs attention',
        value?.error || text || 'The action reported an error.'
      );
    const display = value?.presentation || value;
    if (display.html && display.view && display.view_id) {
      if (display.view_id === current?.view_id) return;
      const template = document.createElement('template');
      template.innerHTML = display.html;
      const content = template.content.querySelector('main');
      if (content?.children.length) {
        current = display;
        main.replaceChildren(...Array.from(content.childNodes, copyMarkup));
        showOpenButton();
        return;
      }
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
    ].filter(Boolean);
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
  window.addEventListener('message', ({ source, data }) => {
    if (source !== parent || data?.jsonrpc !== '2.0') return;
    if (data.id === 1 && data.result) {
      supportsTools = !!data.result.hostCapabilities?.serverTools;
      theme(data.result.hostContext);
      notify('ui/notifications/initialized');
      new ResizeObserver(() =>
        notify('ui/notifications/size-changed', {
          height: Math.ceil(document.body.getBoundingClientRect().height)
        })
      ).observe(document.body);
      showOpenButton();
    } else if (pending && data.id === pending.id) {
      pending.finish(!data.error && !data.result?.isError);
    } else if (data.method === 'ui/notifications/tool-result') {
      accept(data.params);
    } else if (data.method === 'ui/notifications/host-context-changed') {
      theme(data.params);
    }
  });
  parent.postMessage(
    {
      jsonrpc: '2.0',
      id: 1,
      method: 'ui/initialize',
      params: {
        protocolVersion: '2026-01-26',
        appInfo: { name: 'Office Bridge', version: '1.0.0' },
        appCapabilities: {}
      }
    },
    '*'
  );
})();
