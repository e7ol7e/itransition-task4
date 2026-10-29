/* Shared native dashboard controls and AI-returned, validated view state. */
'use strict';
const originalStores=structuredClone(payload.stores);
let dashboardState={store:payload.stores[selected].store,view:'overview',filter_dataset:'orders',filters:[],chart_range:'all'};
let dashboardRevision=0,chatHistory=[],chatBusy=false;
const originalRender=render;
const api=async(path,body)=>{
 const response=await fetch('/api/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
 const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'The request could not be processed.');return result;
};
function element(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
function notice(text,error=false){const e=document.getElementById('view-status');e.textContent=text;e.classList.toggle('chat-error',error)}
function setupControls(){
 const toolbar=document.getElementById('view-toolbar');toolbar.replaceChildren();
 const tabs=element('div','view-tabs');tabs.setAttribute('aria-label','Dashboard view');
 for(const [view,label] of [['overview','Overview'],['books','Catalog'],['users','Customers'],['orders','Orders']]){
  const b=element('button',view===dashboardState.view?'active':'',label);b.type='button';b.setAttribute('aria-pressed',String(view===dashboardState.view));b.onclick=()=>{dashboardState.view=view;refreshDashboard()};tabs.append(b);
 }
 toolbar.append(tabs);
 document.getElementById('chat-scope').textContent=dashboardState.store.replace('STORE','Store ');
 const dataset=dashboardState.view==='overview'?'orders':dashboardState.view;
 const fields={books:['title','author','genre','publisher','year','id'],users:['name','email','phone','address','id','person_id'],orders:['date','title','author','genre','customer_email','user_id','book_id','quantity','paid_price_cents']};
 const field=document.getElementById('filter-field');const prior=field.value;field.replaceChildren(...fields[dataset].map(name=>{const o=element('option','',name.replaceAll('_',' '));o.value=name;return o}));if(fields[dataset].includes(prior))field.value=prior;
 const banner=document.getElementById('filter-banner');banner.classList.toggle('visible',dashboardState.filters.length>0);
 document.getElementById('filter-description').textContent=dashboardState.filters.length?dashboardState.filter_dataset+': '+dashboardState.filters.map(f=>`${f.field} ${f.op.replaceAll('_',' ')} ${Array.isArray(f.value)&&f.value.length>10?'['+f.value.length+' selected values]':JSON.stringify(f.value)}`).join(' AND '):'';
 document.querySelectorAll('[data-range]').forEach(b=>b.addEventListener('click',()=>{dashboardState.chart_range=b.dataset.range}));
}
window.render=function(){
 const changed=dashboardState.store!==payload.stores[selected].store;
 if(changed){payload.stores=structuredClone(originalStores);dashboardState.store=payload.stores[selected].store;dashboardState.filters=[];dashboardState.chart_range='all'}
 originalRender();setupControls();
 document.getElementById('view').hidden=dashboardState.view!=='overview';
 document.getElementById('records-view').hidden=dashboardState.view==='overview';
 if(changed||dashboardState.view!=='overview')refreshDashboard();
};
async function refreshDashboard(){
 const revision=++dashboardRevision;
 setupControls();notice('Updating the view…');
 try{
  const result=await api('dashboard',dashboardState);if(revision!==dashboardRevision)return;
  payload.stores[selected]=result.summary;range=dashboardState.chart_range;
  originalRender();setupControls();
  document.getElementById('view').hidden=dashboardState.view!=='overview';document.getElementById('records-view').hidden=dashboardState.view==='overview';
  const recordSection=document.getElementById('records-view');recordSection.replaceChildren();
  if(dashboardState.view!=='overview'){
   const name={books:'Book catalog',users:'Customer records & aliases',orders:'Orders'}[dashboardState.view];
   const cap=element('div','table-caption');cap.append(element('h2','',name),element('span','pill',number(result.table.result_rows)+' records'));recordSection.append(cap);
   recordSection.append(element('p','sub',result.table.truncated?'Showing the first 100 matching records. Use filters or ask the analyst for an exact total.':'All matching records.'));
   if(!result.table.rows.length)recordSection.append(element('div','empty-state','No records match these filters.'));
   else{
    const wrap=element('div','table-scroll'),table=element('table','record-table'),head=element('thead'),tr=element('tr');
    const columns=Object.keys(result.table.rows[0]).filter(k=>!k.endsWith('_usd'));
    for(const key of columns)tr.append(element('th','',key.replaceAll('_',' ')));head.append(tr);table.append(head);
    const body=element('tbody');for(const row of result.table.rows){const r=element('tr');for(const key of columns){const td=element('td','',row[key]===null?'Unknown':key.endsWith('_cents')?money(row[key]):Array.isArray(row[key])?row[key].join(', '):String(row[key]));if(key==='id'&&dashboardState.view==='users'){const button=element('button','audit-trigger',String(row[key])+' \u2197');button.type='button';button.dataset.proof='person';button.dataset.userId=row[key];td.replaceChildren(button)}r.append(td)}body.append(r)}
    table.append(body);wrap.append(table);recordSection.append(wrap);
   }
  }
  notice('');
 }catch(error){if(revision===dashboardRevision)notice(error.message,true)}
}
async function applyState(state){
 dashboardState=state;selected=payload.stores.findIndex(s=>s.store===state.store);range=state.chart_range;
 history.replaceState(null,'','#'+state.store.toLowerCase());await refreshDashboard();
}
document.getElementById('filter-form').onsubmit=e=>{
 e.preventDefault();const field=document.getElementById('filter-field').value,op=document.getElementById('filter-op').value;
 let value=document.getElementById('filter-value').value.trim();if(!value)return;
 if(['id','person_id','user_id','book_id','quantity','paid_price_cents','year'].includes(field)&&op!=='contains'){value=Number(value);if(!Number.isFinite(value)){notice('Enter a valid number for this field.',true);return}}
 dashboardState.filter_dataset=dashboardState.view==='overview'?'orders':dashboardState.view;
 dashboardState.filters=[{field,op,value}];refreshDashboard();
};
document.getElementById('clear-filters').onclick=()=>{dashboardState.filters=[];document.getElementById('filter-value').value='';refreshDashboard()};
function addMessage(role,text,evidence){
 const container=document.getElementById('chat-messages'),message=element('div','message '+role);
 message.append(element('span','role-label',role==='user'?'You':'Data analyst'),document.createTextNode(text));
 if(evidence?.length){const details=element('details'),summary=element('summary','','Verified data · '+evidence.length+' tool calls');details.append(summary,element('pre','',JSON.stringify(evidence,null,2)));message.append(details)}
 container.append(message);container.scrollTop=container.scrollHeight;return message;
}
document.getElementById('chat-form').onsubmit=async e=>{
 e.preventDefault();if(chatBusy)return;const input=document.getElementById('chat-input'),text=input.value.trim();if(!text)return;
 chatBusy=true;input.value='';document.getElementById('chat-send').disabled=true;addMessage('user',text);
 const pending=addMessage('assistant','Checking the data…');
 try{
  const result=await api('chat',{message:text,state:dashboardState,history:chatHistory.slice(-10)});pending.remove();
  for(const state of result.actions)await applyState(state);
  const message=addMessage('assistant',result.answer,result.evidence);window.addEvidenceButtons?.(message,result.evidence);chatHistory.push({role:'user',content:text},{role:'assistant',content:result.answer.slice(0,6000)});
 }catch(error){pending.textContent=error.message;pending.classList.add('chat-error')}
 finally{chatBusy=false;document.getElementById('chat-send').disabled=false;input.focus()}
};
document.getElementById('chat-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();document.getElementById('chat-form').requestSubmit()}};
for(const button of document.querySelectorAll('[data-prompt]'))button.onclick=()=>{document.getElementById('chat-input').value=button.dataset.prompt;document.getElementById('chat-input').focus()};
addMessage('assistant','Ask a question about a store, or tell me what to show. Counts and totals come from the data tools. Try “Which genre earned the most revenue?” or “Show customers whose emails start with s or p.”');
api('status').then(status=>{document.getElementById('assistant-status').textContent=status.ai_configured?'DeepSeek · connected to verified data':'AI connection is being configured';}).catch(()=>{document.getElementById('assistant-status').textContent='Live chat requires the running dashboard service';});
render();
