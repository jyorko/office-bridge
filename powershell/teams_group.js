(phase, body) => {
 const target='QA-ASSIST-VERINT-DEPLOY';
 const avatar=document.querySelector('[data-tid="me-control-avatar-trigger"]');
 if(location.hostname!=='teams.cloud.microsoft'||!avatar?.getAttribute('aria-label')?.includes('joshua.yorko@gainwelltechnologies.com')) return {state:'identity_failed'};
 const editors=Array.from(document.querySelectorAll('[data-tid="ckeditor"][contenteditable="true"]')).filter(e=>e.getClientRects().length);
 if(phase==='navigate') {
  if(editors.some(e=>e.innerText.trim())) return {state:'draft_exists'};
  const tab=document.querySelector('[data-tid="app-bar-wrapper"] button[aria-label="Chat"]'); if(tab)tab.click();
  const all=Array.from(document.querySelectorAll('[role="treeitem"]')).filter(e=>Array.from(e.querySelectorAll('*')).some(n=>n.children.length===0&&n.textContent.trim()===target));
  const hits=all.filter(e=>!all.some(n=>n!==e&&e.contains(n)));
  if(hits.length!==1) return {state:'waiting'};
  hits[0].click();return {state:'navigated'};
 }
 const title=document.querySelector('[data-tid="chat-title"]');
 if(title?.textContent.trim()!==target) return {state:'waiting'};
 if(phase==='prepare') {
  if(editors.length!==1)return {state:'waiting'};
  if(editors[0].innerText.trim())return {state:'draft_exists'};
  window.__officeBefore=Array.from(document.querySelectorAll('[data-tid="chat-pane-message"]')).map(e=>e.id);
  window.__officeClicked=false;editors[0].focus();return {state:document.activeElement===editors[0]?'prepared':'failed'};
 }
 if(phase==='send') {
  if(editors.length!==1||editors[0].innerText.trim()!==body||window.__officeClicked!==false||!Array.isArray(window.__officeBefore))return {state:'failed'};
  const buttons=Array.from(document.querySelectorAll('button[data-tid="newMessageCommands-send"],button[data-tid="sendMessageCommands-send"]')).filter(e=>e.getClientRects().length&&!e.disabled&&e.getAttribute('aria-disabled')!=='true');
  if(buttons.length!==1)return {state:'waiting'};
  window.__officeClicked=true;buttons[0].click();return {state:'clicked'};
 }
 if(phase==='observe') {
  const matches=Array.from(document.querySelectorAll('[data-tid="chat-pane-message"]')).filter(e=>!window.__officeBefore.includes(e.id)&&e.innerText.trim()===body&&/^message-body-[0-9]+$/.test(e.id));
  if(matches.length!==1)return {state:'waiting'};
  let p=matches[0];for(let i=0;p&&i<5;i++,p=p.parentElement){const author=p.querySelector('[data-tid="message-author-name"]');const stamp=p.querySelector('time');if(author&&stamp&&author.textContent.trim()==='Yorko, Joshua')return {state:'observed',message_id:matches[0].id,time:stamp.getAttribute('datetime')};}
  return {state:'waiting'};
 }
 return {state:'failed'};
}
