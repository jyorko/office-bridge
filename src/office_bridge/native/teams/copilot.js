(request) => {
  if (location.hostname !== 'm365copilotapp.svc.cloud.microsoft' ||
      !/^\/chat(?:\/conversation\/[0-9a-f-]{36})?\/?$/i.test(location.pathname))
    return {state: 'unsupported_surface'};
  const visible = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const all = selector => Array.from(document.querySelectorAll(selector)).filter(visible);
  const meta = e => ({tag:e.tagName.toLowerCase(),id:e.id||'',role:e.getAttribute('role')||'',
    testid:e.getAttribute('data-testid')||'',tid:e.getAttribute('data-tid')||'',
    label:(e.getAttribute('aria-label')||e.getAttribute('title')||'').slice(0,160),
    text:(e.innerText||'').slice(0,120),checked:e.getAttribute('aria-checked'),
    editable:e.getAttribute('contenteditable'),disabled:!!e.disabled});
  const editors = all('#m365-chat-editor-target-element[role="textbox"][aria-label="Message Copilot"]');
  const work = all('[role="switch"]').filter(e => /Work IQ/i.test(e.getAttribute('aria-label')||''));
  const workEnabled = work.length === 1 && work[0].getAttribute('aria-checked') === 'true' &&
      work[0].getAttribute('aria-label') === 'Turn off Work IQ';
  const editor = editors.length === 1 ? editors[0] : null;
  const modelButtons=all('button').filter(e=>(e.getAttribute('aria-label')||'').endsWith(', Model Selector'));
  const modelButton=modelButtons.length===1?modelButtons[0]:null;
  const currentModel=modelButton?(modelButton.getAttribute('aria-label')||'').slice(0,-', Model Selector'.length):null;
  const text = e => {
    let value=(e.innerText || e.textContent || '').replace(/\r\n/g,'\n').trim();
    const sentinel=e.querySelector('[aria-hidden="true"][data-lexical-text="true"]:last-child');
    if (sentinel && sentinel.textContent==='\u200b\u200c' && value.endsWith(sentinel.textContent))
      value=value.slice(0,-2);
    return value.trim();
  };
  const active=window.__officeCopilotQuery;
  if (request.phase === 'status') return {state:editor && workEnabled &&
      !(active && active.clicked && !active.completed) ? 'ready' : 'not_ready',
    work_iq_enabled:workEnabled, composer_nonempty:!!editor && text(editor).length>0,model:currentModel};
  if (request.phase === 'models' || request.phase === 'select_model') {
    if (!modelButton || modelButton.disabled || modelButton.getAttribute('aria-disabled')==='true' ||
        !editor || !workEnabled) return {state:'model_selector_unavailable'};
    if (request.phase==='select_model') {
      if (currentModel===request.model) return {state:'model_selected',model:currentModel};
      if (text(editor)) return {state:'draft_exists'};
      if (active && active.clicked && !active.completed) return {state:'query_in_progress'};
    }
    const menuSelector='[role="menu"],[role="listbox"]';
    const enabled=e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true';
    const branch=e=>/^(menu|true)$/.test(e.getAttribute('aria-haspopup')||'');
    const options=menu=>Array.from(menu.querySelectorAll(
      '[role="menuitemradio"],[role="option"],[role="menuitem"]')).filter(e=>
        visible(e)&&e.closest(menuSelector)===menu);
    const entries=menu=>options(menu).filter(e=>!branch(e)).map(e=>({
      element:e,name:(e.getAttribute('aria-label')||text(e)).split('\n')[0].trim().slice(0,120),
      selected:e.getAttribute('aria-checked')==='true'||e.getAttribute('aria-selected')==='true'||
        currentModel===(e.getAttribute('aria-label')||text(e)).split('\n')[0].trim(),
      enabled:enabled(e)})).filter(e=>e.name);
    const relatedMenus=button=>{
      const controlled=button.getAttribute('aria-controls');
      const controlledMenu=controlled?document.getElementById(controlled):null;
      if (controlledMenu&&visible(controlledMenu)&&controlledMenu.matches(menuSelector)) return [controlledMenu];
      return button.id?all(menuSelector).filter(e=>e.getAttribute('aria-labelledby')===button.id):[];
    };
    let state=window.__officeCopilotModelMenu;
    const close=()=>{
      if (state?.owned&&state.root&&visible(state.root)) modelButton.click();
      window.__officeCopilotModelMenu=null;
    };
    const fail=reason=>{close();return {state:reason};};
    if (state&&Date.now()-state.startedAt>8000) return fail('model_menu_unverified');
    if (!state) {
      const roots=relatedMenus(modelButton);
      if (roots.length>1) return {state:'model_menu_ambiguous'};
      state=window.__officeCopilotModelMenu={startedAt:Date.now(),owned:!roots.length,
        root:roots[0]||null,groups:null,index:0,items:[],pending:null};
      if (!roots.length) {modelButton.click();return {state:'model_menu_opening'};}
    }
    if (!state.root) {
      let roots=relatedMenus(modelButton);
      if (!roots.length&&state.owned) roots=all(menuSelector);
      if (!roots.length) return {state:'model_menu_opening'};
      if (roots.length!==1) return fail('model_menu_ambiguous');
      state.root=roots[0];
    }
    if (!visible(state.root)) return fail('model_menu_unverified');
    let currentEntries;
    if (state.pending) {
      let children=relatedMenus(state.pending);
      if (!children.length) children=all(menuSelector).filter(e=>e!==state.root);
      if (!children.length) return {state:'model_menu_opening'};
      if (children.length!==1) return fail('model_menu_ambiguous');
      if (options(children[0]).some(branch)) return fail('model_menu_unverified');
      currentEntries=entries(children[0]);
      if (!currentEntries.length) return fail('model_menu_unverified');
      state.pending=null;
    } else if (!state.groups) {
      currentEntries=entries(state.root);
      state.groups=options(state.root).filter(e=>branch(e)&&enabled(e));
      if (state.groups.length>8) return fail('model_menu_unverified');
    } else currentEntries=[];
    if (request.phase==='select_model') {
      const selected=currentEntries.filter(e=>e.name===request.model&&e.enabled);
      if (selected.length>1) return fail('model_menu_ambiguous');
      if (selected.length===1) {
        selected[0].element.click();window.__officeCopilotModelMenu=null;
        return {state:'model_selection_pending'};
      }
    }
    state.items.push(...currentEntries.map(({element,...item})=>item));
    if (state.items.length>30) return fail('model_menu_unverified');
    if (state.index<state.groups.length) {
      state.pending=state.groups[state.index++];
      if (!visible(state.pending)) return fail('model_menu_unverified');
      state.pending.click();return {state:'model_menu_opening'};
    }
    if (request.phase==='select_model') return fail('model_unavailable');
    if (!state.items.length||new Set(state.items.map(e=>e.name)).size!==state.items.length)
      return fail('model_menu_unverified');
    const items=state.items;close();
    return {state:'models',model:currentModel,models:items};
  }
  if (request.phase === 'verify_prepare') {
    const previous=window.__officeCopilotQuery;
    if (!previous || previous.requestId!==request.requestId ||
        previous.question!==request.question) return {state:'request_missing'};
    if (previous.clicked) return {state:'already_clicked'};
    if (!editor || !workEnabled) return {state:'not_ready'};
    return {state:text(editor)===previous.question.trim() ? 'prepared' : 'input_unverified'};
  }
  if (request.phase === 'prepare') {
    if (!editor || !workEnabled || editor.getAttribute('data-lexical-editor') !== 'true')
      return {state:'not_ready'};
    if (typeof request.question !== 'string' || request.question.length<1 || request.question.length>2000 ||
        !/^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/.test(request.requestId||'')) return {state:'invalid_request'};
    const previous = window.__officeCopilotQuery;
    if (previous && previous.requestId === request.requestId) {
      if (previous.question!==request.question || text(editor)!==request.question.trim())
        return {state:'draft_changed'};
      return {state:previous.clicked ? 'already_clicked' : 'prepared'};
    }
    if (previous && previous.clicked && !previous.completed) return {state:'query_in_progress'};
    if (text(editor) || editor.querySelector('img,a,table,video')) return {state:'draft_exists'};
    window.__officeCopilotQuery = {requestId:request.requestId,question:request.question,
      clicked:false,startedAt:Date.now(),lastAnswer:'',stableSince:0,
      model:currentModel,
      beforeTurns:all('[data-testid="m365-chat-llm-web-ui-chat-message"]').map(e=>e.id)};
    editor.focus({preventScroll:true});
    if (!document.execCommand('insertText',false,request.question)) return {state:'input_failed'};
    return {state:text(editor)===request.question.trim() ? 'prepared' : 'input_unverified'};
  }
  const pending = window.__officeCopilotQuery;
  if (request.phase === 'submit') {
    if (!pending || pending.requestId!==request.requestId) return {state:'request_missing'};
    if (pending.clicked) return {state:'already_clicked'};
    if (!editor || !workEnabled || text(editor)!==pending.question.trim()) return {state:'draft_changed'};
    const sends=all('button').filter(e=>/^(Send|Send message|Submit|Send prompt)$/i.test(
      (e.getAttribute('aria-label')||e.getAttribute('title')||'').trim()) && !e.disabled &&
      e.getAttribute('aria-disabled')!=='true');
    if (sends.length!==1) return {state:'submit_unavailable'};
    pending.clicked=true;
    sends[0].click();
    return {state:'clicked',request_id:pending.requestId,model:pending.model};
  }
  if (request.phase === 'read') {
    if (!pending || pending.requestId!==request.requestId) return {state:'request_missing'};
    if (!pending.clicked) return {state:'not_submitted',request_id:pending.requestId,
      question_matches:!!editor && text(editor)===pending.question.trim(),work_iq_enabled:workEnabled,
      submission_verified:false};
    const turns=all('[data-testid="m365-chat-llm-web-ui-chat-message"]').filter(turn=>{
      if ((pending.beforeTurns||[]).includes(turn.id)) return false;
      const response=turn.querySelector('[data-testid="copilot-message-div"]');
      return Array.from(turn.querySelectorAll('div,p,span')).some(e=>
        (!response || !response.contains(e)) && text(e)===pending.question.trim());
    });
    const waiting={state:'awaiting_response',request_id:pending.requestId,
      work_iq_enabled:workEnabled,submission_verified:turns.length===1};
    if (turns.length!==1) return waiting;
    const turn=turns[0], response=turn.querySelector('[data-testid="copilot-message-div"]');
    const replies=response ? Array.from(response.querySelectorAll('[data-testid="markdown-reply"]')).filter(visible) : [];
    const copy=Array.from(turn.querySelectorAll('button[data-testid="CopyButtonTestId"]')).filter(e=>
      visible(e) && e.getAttribute('aria-label')==='Copy Response' && !e.disabled);
    const stop=all('button').some(e=>/^Stop(?: generating| responding| response)?$/i.test(e.getAttribute('aria-label')||''));
    if (!workEnabled || !response || !/^chatMessageResponse-[A-Za-z0-9_-]{1,128}$/.test(response.id) ||
        replies.length!==1 || copy.length!==1 || stop || turn.matches('[aria-busy="true"]') ||
        turn.querySelector('[aria-busy="true"]')) return waiting;
    const answer=text(replies[0]);
    if (!answer || answer.length>32000) return waiting;
    const signature=response.id+'\n'+answer;
    if (pending.lastAnswer!==signature) {
      pending.lastAnswer=signature;pending.stableSince=Date.now();return waiting;
    }
    if (Date.now()-pending.stableSince<2000) return waiting;
    const citations=[],seen=new Set();
    for (const anchor of replies[0].querySelectorAll('a[href]')) {
      let url;try {url=new URL(anchor.href,location.href);} catch (_) {continue;}
      if (!/^https?:$/.test(url.protocol) || seen.has(url.href)) continue;
      seen.add(url.href);
      citations.push({title:(text(anchor)||anchor.getAttribute('aria-label')||url.hostname).slice(0,500),url:url.href});
      if (citations.length>=50) break;
    }
    pending.completed=true;
    return {state:'completed',request_id:pending.requestId,submission_verified:true,
      work_iq_enabled:true,answer,citations,citations_complete:false,
      conversation_url:location.origin+location.pathname,response_id:response.id,model:pending.model};
  }
  if (request.phase === 'inspect') {
    const controls = all('button,a,[role="button"]');
    const editor = editors.length === 1 ? editors[0] : null;
    const ancestors = [];
    for (let e=editor, i=0; e && i<5; e=e.parentElement,i++)
      ancestors.push({tag:e.tagName.toLowerCase(),id:e.id||'',class:e.className||'',
        attrs:Array.from(e.attributes).filter(a=>/^(data-|aria-|contenteditable|role)/.test(a.name))
          .map(a=>[a.name,a.value]).slice(0,20)});
    return {state:'inspected',editors:editors.map(meta),work_iq:work.map(meta),ancestors,
      model_menus:all('[role="menu"],[role="listbox"]').map(menu=>({
        ...meta(menu),labelled_by:menu.getAttribute('aria-labelledby'),
        options:Array.from(menu.querySelectorAll('[role^="menuitem"],[role="option"]')).filter(visible)
          .map(e=>({...meta(e),has_popup:e.getAttribute('aria-haspopup'),
            controls:e.getAttribute('aria-controls'),expanded:e.getAttribute('aria-expanded')}))})),
      query:window.__officeCopilotQuery ? {request_id:window.__officeCopilotQuery.requestId,clicked:window.__officeCopilotQuery.clicked} : null,
      buttons:controls.filter(e=>!/^(More|Chats)$/.test(e.getAttribute('aria-label')||e.innerText||''))
        .slice(-50).map(meta),
      account_controls:controls.filter(e=>/account|profile|sign.in|sign.out/i.test(
        [e.id,e.getAttribute('aria-label'),e.getAttribute('data-testid')].join(' '))).slice(0,20).map(meta),
      message_nodes:all('[data-message-id],[data-content],[data-testid*="message"],[data-testid*="response"]')
        .slice(-30).map(e=>({tag:e.tagName.toLowerCase(),id:e.id||'',role:e.getAttribute('role')||'',
          testid:e.getAttribute('data-testid')||'',class:e.className||''})),
      turns:all('[data-testid="m365-chat-llm-web-ui-chat-message"]').slice(-3).map(e=>({
        id:e.id,attrs:Array.from(e.attributes).filter(a=>/^(data-|aria-|role)/.test(a.name)).map(a=>[a.name,a.value]),
        question_matches:!!pending && Array.from(e.querySelectorAll('div,p,span')).some(n=>text(n)===pending.question.trim()),
        reply_length:(e.querySelector('[data-testid="markdown-reply"]')?.innerText||'').length,
        response_attrs:Array.from(e.querySelector('[data-testid="copilot-message-div"]')?.attributes||[]).map(a=>[a.name,a.value]),
        loading_text:Array.from(e.querySelectorAll('[data-testid="loading-message"],[aria-busy]')).map(n=>({text:text(n).slice(0,200),busy:n.getAttribute('aria-busy')})),
        links:Array.from(e.querySelectorAll('a[href]')).slice(0,40).map(a=>({text:text(a).slice(0,100),href:a.href,testid:a.getAttribute('data-testid')})),
        buttons:Array.from(e.querySelectorAll('button,[role="button"]')).filter(visible).slice(-20).map(meta)})),
      composer_nonempty:!!editor && (editor.innerText||editor.textContent||'').trim().length>0};
  }
  return {state:'unsupported_phase'};
}
