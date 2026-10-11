/* Visitor requests and administrator review. Customer rules stay in the user centre. */
(function () {
  'use strict';
  let publicState = null, adminState = null;
  const statuses = {pending:'待审核',approved:'已加入追更',rejected:'未通过',uncertain:'结果待核查'};
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const text = (value, limit = 160) => typeof value === 'string' ? value.trim().slice(0,limit) : '';
  const validId = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value);
  const validToken = value => typeof value === 'string' && value.length >= 20 && value.length <= 4096 && /^[A-Za-z0-9_.=-]+$/.test(value);
  const live = state => state && !state.stopped && state.root.isConnected;
  function record(raw) {
    if (!raw || !validId(raw.id) || !text(raw.title) || !statuses[raw.status]) return null;
    return {id:raw.id,title:text(raw.title),type:raw.media_type==='tv'?'剧集':'电影',status:raw.status,note:text(raw.note,200),reviewNote:text(raw.review_note,300),createdAt:text(raw.created_at,40),count:Number.isInteger(raw.requester_count)&&raw.requester_count>0?Math.min(raw.requester_count,100000):1};
  }
  const date = value => { const parsed=new Date(value);return Number.isFinite(parsed.getTime())?parsed.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):''; };
  function statusMarkup(row) { return `<span class="subscription-badge is-${row.status}">${statuses[row.status]}</span>`; }
  function failure(error, mutation=false) {
    if(error?.status===401)return '登录状态已失效，请重新登录后再操作。';
    if(error?.status===403)return '当前操作未获允许，请刷新页面后重试。';
    if(error?.status===429)return '操作比较频繁，请稍后再试。';
    if(error?.status===400||error?.status===422)return '影片信息已过期或填写不正确，请重新搜索后提交。';
    if(error?.status===409)return '状态已发生变化，请刷新记录核对处理结果。';
    if(mutation)return '暂未确认操作结果，请刷新记录核对，避免重复提交。';
    return '暂时无法读取，请稍后重新读取。';
  }
  async function request(state,path,body) {
    const controller=new AbortController();if(body===undefined){state.readController?.abort();state.readController=controller;}state.controllers.add(controller);
    const timer=setTimeout(()=>controller.abort(),body===undefined?20000:60000);
    try {
      const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json',...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:controller.signal});
      const data=await response.json().catch(()=>({}));
      if(!response.ok||data.error){const error=new Error('Request failed');error.status=response.status;error.code=data.error?.code;throw error;}
      return data;
    } finally {clearTimeout(timer);state.controllers.delete(controller);}
  }
  function cardAction(item) {
    if(!item || !text(item.title) || !['tv','movie'].includes(item.type) || !/^\d{1,12}$/.test(String(item.id||'')) || !validToken(item.requestToken))return '';
    return `<button type="button" class="subscription-card-action" data-subscription-action="request" data-subscription-id="${escape(item.id)}" data-subscription-type="${item.type}" data-subscription-title="${escape(text(item.title))}" data-subscription-token="${escape(item.requestToken)}">想看 / 订阅 <span aria-hidden="true">＋</span></button>`;
  }
  function publicMarkup() {
    return `<section class="subscription-public" aria-label="想看申请与客户订阅"><div class="subscription-intro"><div><span class="subscription-kicker">YOUR WATCHLIST</span><h2>下一部，由你提名。</h2><p>访客提交想看申请，人工审核后加入追更。已有客户连接用户中心，继续按原有额度与审核规则订阅。</p></div><div class="subscription-customer" data-subscription-customer><p>尚未连接用户中心</p><button type="button" class="btn btn-outline" data-subscription-action="connect">连接用户中心</button></div></div><p class="subscription-feedback" data-subscription-feedback role="status"></p><details class="subscription-history"><summary>我的想看申请 <span data-subscription-count></span></summary><div class="subscription-history-toolbar"><p>仅显示本浏览器提交的申请；清除浏览器数据后将无法查看原记录。</p><button type="button" class="btn btn-outline btn-small" data-subscription-action="refresh">刷新记录</button></div><div data-subscription-history aria-live="polite"><p class="subscription-empty">正在读取申请记录…</p></div></details></section>`;
  }
  function customerAdapter() {
    const bridge=window.MediaDockCustomerBridge;
    return bridge && typeof bridge.connect==='function' && typeof bridge.status==='function' && typeof bridge.subscribe==='function' ? bridge : null;
  }
  function customerResult(value) {
    if(value?.connected===true){const remaining=Number.isInteger(value.remaining)&&value.remaining>=0?value.remaining:Number.isInteger(value.daily_limit)&&value.daily_limit>=0&&Number.isInteger(value.daily_used)&&value.daily_used>=0?Math.max(0,value.daily_limit-value.daily_used):null;const quota=remaining!==null?`今日剩余 ${remaining} 次`:'订阅沿用用户中心的额度与审核规则';return {connected:true,quota};}
    return {connected:false,reason:['login_required','customer_required'].includes(value?.reason)?value.reason:'unavailable'};
  }
  function renderCustomer(state) {
    if(!live(state))return;
    const panel=state.root.querySelector('[data-subscription-customer]');if(!panel)return;
    if(!state.customerReady){panel.innerHTML='<p role="status">正在检查用户中心登录…</p><small>确认后选择客户订阅或访客想看申请。</small><button type="button" class="btn btn-outline" disabled>正在检查</button>';return;}
    const connected=state.customer?.connected;
    panel.innerHTML=connected?`<p><span class="subscription-connected-dot" aria-hidden="true"></span>已连接${'用户中心'}</p><small>${escape(state.customer.quota||'订阅沿用用户中心的额度与审核规则')}</small><button type="button" class="subscription-text-button" data-subscription-action="connect">重新核对登录</button>`:`<p>尚未连接用户中心</p><button type="button" class="btn btn-outline" data-subscription-action="connect" ${!customerAdapter()?'disabled':''}>连接用户中心</button>${!customerAdapter()?'<small>连接入口准备中，可先提交想看申请。</small>':['login_required','customer_required'].includes(state.customer?.reason)?'<button type="button" class="subscription-login-link" data-subscription-action="connect">连接后前往用户中心登录</button><small>使用客户账号登录后点击连接；访客也可提交想看申请。</small>':''}`;
  }
  function publicFeedback(state,value){if(live(state))state.root.querySelector('[data-subscription-feedback]').textContent=value;}
  function renderHistory(state) {
    if(!live(state))return;
    state.root.querySelector('[data-subscription-count]').textContent=state.records.length?String(state.records.length):'';
    state.root.querySelector('[data-subscription-history]').innerHTML=state.records.length?`<div class="subscription-history-list">${state.records.map(row=>`<article class="subscription-history-item"><div><h3>${escape(row.title)}</h3><p>${row.type}${date(row.createdAt)?` · ${escape(date(row.createdAt))}`:''}</p>${row.reviewNote?`<p class="subscription-review-note">审核说明：${escape(row.reviewNote)}</p>`:''}${row.status==='uncertain'?'<p class="subscription-review-note">管理员正在核对追更结果，无需再次申请。</p>':''}</div>${statusMarkup(row)}</article>`).join('')}</div>`:'<p class="subscription-empty">还没有想看申请。在下方搜索影片后，点击“想看 / 订阅”。</p>';
  }
  async function loadHistory(state) {
    const seq=++state.readSequence;
    try {const data=await request(state,'/api/public/subscription-requests');if(!live(state)||seq!==state.readSequence)return;state.ready=true;state.configured=data.configured===true;state.records=(Array.isArray(data.items)?data.items:[]).slice(0,100).map(record).filter(Boolean);renderHistory(state);}
    catch(error){if(!live(state)||seq!==state.readSequence)return;state.ready=false;state.root.querySelector('[data-subscription-history]').innerHTML='<p class="subscription-empty">暂时无法读取申请记录，请点击刷新记录。</p>';publicFeedback(state,failure(error));}
  }
  async function connectCustomer(state,button) {
    if(state.connecting)return;
    const adapter=customerAdapter();if(!adapter){publicFeedback(state,'用户中心连接尚未就绪，可以先提交想看申请。');return;}
    state.connecting=true;const sequence=++state.customerSequence;button.disabled=true;publicFeedback(state,'正在核对用户中心登录状态…');
    try {const value=await adapter.connect();if(!live(state)||sequence!==state.customerSequence)return;state.customer=customerResult(value);renderCustomer(state);publicFeedback(state,state.customer.connected?'已连接用户中心，订阅将按原有规则处理。':'尚未连接，可重新连接或提交想看申请。');}
    catch {if(live(state))publicFeedback(state,'暂时无法连接用户中心，可重试或先提交想看申请。');}
    finally{state.connecting=false;if(button.isConnected)button.disabled=false;}
  }
  function closeDialog(state) {
    if(!state.dialog)return;
    state.dialog.remove();state.dialog=null;
    if(state.returnFocus?.isConnected)state.returnFocus.focus();state.returnFocus=null;
  }
  function dialog(state,content,opener) {
    closeDialog(state);state.returnFocus=opener;
    const overlay=document.createElement('div');overlay.className='subscription-modal';
    overlay.innerHTML=`<section class="subscription-dialog" role="dialog" aria-modal="true" aria-labelledby="subscription-dialog-title" tabindex="-1"><button type="button" class="subscription-dialog-close" data-subscription-close aria-label="关闭">×</button>${content}</section>`;
    document.body.append(overlay);state.dialog=overlay;
    overlay.addEventListener('click',event=>{if(event.target===overlay||event.target.closest('[data-subscription-close]')){if(!state.mutating)closeDialog(state);}});
    overlay.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&!state.mutating){event.preventDefault();closeDialog(state);}
      if(event.key==='Tab'){const items=[...overlay.querySelectorAll('button:not(:disabled),textarea,input,select,a[href],[tabindex="0"]')].filter(el=>!el.hidden);if(!items.length){event.preventDefault();return;}const first=items[0],last=items.at(-1);if(event.shiftKey&&(document.activeElement===first||document.activeElement===overlay.querySelector('.subscription-dialog'))){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
    });
    overlay.querySelector('.subscription-dialog').focus();return overlay;
  }
  async function requestAfterIdentity(state,button) {
    if(state.opening)return;
    state.opening=true;
    const original=button.innerHTML;
    try {
      if(!state.customerReady){button.disabled=true;button.textContent='核对登录中…';await state.customerCheck;}
      if(live(state)&&button.isConnected)openRequest(state,button);
    } finally {state.opening=false;if(button.isConnected){button.disabled=false;button.innerHTML=original;}}
  }
  function openRequest(state,button) {
    const item={id:button.dataset.subscriptionId,type:button.dataset.subscriptionType,title:text(button.dataset.subscriptionTitle),requestToken:button.dataset.subscriptionToken};
    if(!cardAction(item))return;
    const connected=state.customer?.connected===true;
    const overlay=dialog(state,`<span class="subscription-kicker">${connected?'CUSTOMER SUBSCRIPTION':'WANT TO WATCH'}</span><h2 id="subscription-dialog-title">${connected?'订阅影片':'提交想看申请'}</h2><p class="subscription-dialog-title">${escape(item.title)} <small>${item.type==='tv'?'剧集':'电影'}</small></p><p class="subscription-dialog-help">${connected?'本次订阅使用已连接的用户中心账号，额度与是否需要审核由原有规则决定。':'申请会先进入人工审核；通过后加入 NextFind 追更。提交申请不代表已经入库。'}</p><form data-subscription-submit><label class="subscription-note-label" ${connected?'hidden':''}>补充说明 <span>选填</span><textarea name="note" maxlength="200" rows="3" placeholder="例如想看的季数或版本，不要填写账号密码。" ${connected?'disabled':''}></textarea></label><p class="subscription-form-feedback" role="status"></p><button type="submit" class="btn btn-primary" ${!connected&&(!state.ready||!state.configured)?'disabled':''}>${connected?'按用户中心规则订阅':'提交，等待审核'}</button>${!connected&&(!state.ready||!state.configured)?'<p class="subscription-dialog-help">申请服务暂不可用，请关闭后刷新申请记录。</p>':''}</form>`,button);
    overlay.querySelector('form').addEventListener('submit',event=>{event.preventDefault();event.stopPropagation();void submitRequest(state,item,connected,event.currentTarget);});
  }
  async function submitRequest(state,item,connected,form) {
    if(state.mutating||!live(state))return;
    if(!connected&&(!state.ready||!state.configured))return;
    const button=form.querySelector('[type="submit"]'),feedback=form.querySelector('.subscription-form-feedback');
    state.mutating=true;button.disabled=true;feedback.textContent='正在提交…';
    try {
      if(connected){const adapter=customerAdapter();if(!adapter)throw new Error('Bridge unavailable');const value=await adapter.subscribe({tmdb_id:item.id,media_type:item.type,title:item.title,request_token:item.requestToken});if(!live(state)||!form.isConnected)return;const labels={subscribed:'已加入追更',pending:'已提交审核',duplicate:'已在订阅中',rejected:'未通过原有规则',error:'未完成',uncertain:'结果待核查'};button.textContent=labels[value?.status]||'结果待核查';feedback.textContent=text(value?.message,200)||(value?.status==='pending'?'申请已进入用户中心审核。':value?.status==='subscribed'||value?.status==='duplicate'?'用户中心已确认订阅。':'请前往用户中心核对订阅结果。');if(Number.isInteger(value?.remaining)&&value.remaining>=0){state.customer.quota=`今日剩余 ${value.remaining} 次`;renderCustomer(state);}else{const sequence=++state.customerSequence;Promise.resolve().then(()=>adapter.status()).then(result=>{if(live(state)&&sequence===state.customerSequence){state.customer=customerResult(result);renderCustomer(state);}}).catch(()=>{});}}
      else{const value=await request(state,'/api/public/subscription-requests',{request_token:item.requestToken,note:text(form.elements.note.value,200)});if(!live(state)||!form.isConnected)return;const row=record(value.request);feedback.textContent=value.duplicate?'你已经提交过这部影片，可在“我的想看申请”查看进度。':row?.status==='approved'?'这部影片已加入追更。':'申请已提交，等待人工审核。';void loadHistory(state);}
      if(!connected)button.textContent='已提交';form.querySelector('textarea')?.setAttribute('disabled','');
    } catch(error) {if(live(state)&&form.isConnected){feedback.textContent=failure(error,true);button.textContent='结果待核对';publicFeedback(state,'提交结果尚未确认，请刷新申请记录核对；客户订阅可在用户中心查看。');if(!connected)void loadHistory(state);}}
    finally {state.mutating=false;}
  }
  function mountPublic(root) {
    stopPublic();if(!root?.querySelector('[data-subscription-history]'))return;
    const state={root,controllers:new Set(),records:[],readSequence:0,ready:false,configured:false,customer:{connected:false},customerReady:!customerAdapter(),customerCheck:null,customerSequence:0,stopped:false,mutating:false};publicState=state;
    const click=event=>{if(!(event.target instanceof Element))return;const button=event.target.closest('[data-subscription-action]');if(!button||!root.contains(button)||button.disabled)return;event.preventDefault();const action=button.dataset.subscriptionAction;if(action==='request')void requestAfterIdentity(state,button);else if(action==='refresh'){publicFeedback(state,'');void loadHistory(state);}else if(action==='connect')void connectCustomer(state,button);};
    root.addEventListener('click',click);state.cleanup=()=>root.removeEventListener('click',click);renderCustomer(state);void loadHistory(state);
    const adapter=customerAdapter(),customerSequence=++state.customerSequence;state.customerCheck=adapter?Promise.resolve().then(()=>adapter.status()).then(value=>{if(live(state)&&customerSequence===state.customerSequence)state.customer=customerResult(value);}).catch(()=>{}).finally(()=>{if(live(state)){state.customerReady=true;renderCustomer(state);}}):Promise.resolve();
  }
  function adminMarkup() {
    return `<section class="subscription-admin"><div class="admin-page-title"><div><h1>订阅审核</h1><p>集中处理访客想看申请。客户订阅继续由用户中心的原有规则处理。</p></div><button type="button" class="btn btn-outline" data-subscription-admin="refresh">刷新列表</button></div><div class="subscription-filters" role="group" aria-label="申请审核状态">${[['pending','待审核'],['uncertain','待核查'],['approved','已追更'],['rejected','未通过'],['all','全部']].map(([value,label])=>`<button type="button" data-subscription-filter="${value}" aria-pressed="${value==='pending'}">${label}<span data-subscription-total="${value}"></span></button>`).join('')}</div><p class="subscription-admin-notice">同一影片的想看申请汇总人数，审核通过后只建立一个追更任务。结果待核查时，请先核对状态。</p><p class="subscription-feedback" data-subscription-admin-feedback role="status"></p><div data-subscription-admin-list aria-busy="true"><p class="subscription-empty">正在读取申请…</p></div><div class="subscription-pagination" data-subscription-admin-pages hidden></div></section>`;
  }
  function adminFeedback(state,value){if(live(state))state.root.querySelector('[data-subscription-admin-feedback]').textContent=value;}
  function renderAdmin(state,data) {
    if(!live(state))return;
    state.records=(Array.isArray(data.items)?data.items:[]).slice(0,50).map(record).filter(Boolean);state.hasMore=data.has_more===true;
    const totals=data.counts&&typeof data.counts==='object'?data.counts:{};
    for(const element of state.root.querySelectorAll('[data-subscription-total]')){const n=totals[element.dataset.subscriptionTotal];element.textContent=Number.isInteger(n)&&n>=0?String(n):'';}
    for(const button of state.root.querySelectorAll('[data-subscription-filter]'))button.setAttribute('aria-pressed',String(button.dataset.subscriptionFilter===state.filter));
    state.root.querySelector('[data-subscription-admin-list]').innerHTML=state.records.length?`<div class="subscription-review-list">${state.records.map(row=>`<article class="subscription-review-item"><div class="subscription-review-heading"><div><h2>${escape(row.title)}</h2><p>${row.type} · ${row.count} 人想看${date(row.createdAt)?` · ${escape(date(row.createdAt))}`:''}</p></div>${statusMarkup(row)}</div>${row.note?`<p class="subscription-visitor-note">${escape(row.note)}</p>`:''}${row.reviewNote?`<p class="subscription-review-note">审核说明：${escape(row.reviewNote)}</p>`:''}<div class="subscription-review-actions">${row.status==='pending'?`<button type="button" class="btn btn-primary btn-small" data-subscription-review="approve" data-subscription-record="${row.id}">通过并加入追更</button><button type="button" class="btn btn-outline btn-small" data-subscription-review="reject" data-subscription-record="${row.id}">不通过</button>`:row.status==='uncertain'?`<p>上次操作结果尚未确认。核对时仅查询现有任务。</p><button type="button" class="btn btn-outline btn-small" data-subscription-review="approve" data-subscription-record="${row.id}">核对订阅结果</button>`:''}</div></article>`).join('')}</div>`:'<div class="subscription-empty">当前没有此状态的申请。</div>';
    const pages=state.root.querySelector('[data-subscription-admin-pages]');pages.hidden=!state.hasMore&&state.page===1;pages.innerHTML=`<button type="button" class="btn btn-outline btn-small" data-subscription-admin="prev" ${state.page<=1?'disabled':''}>上一页</button><span>第 ${state.page} 页</span><button type="button" class="btn btn-outline btn-small" data-subscription-admin="next" ${!state.hasMore?'disabled':''}>下一页</button>`;
  }
  async function loadAdmin(state) {
    const seq=++state.readSequence;state.loading=true;state.root.querySelector('[data-subscription-admin-list]').setAttribute('aria-busy','true');
    try {const data=await request(state,`/api/admin/subscription-requests?status=${state.filter}&page=${state.page}`);if(!live(state)||seq!==state.readSequence)return;renderAdmin(state,data);}
    catch(error){if(live(state)&&seq===state.readSequence){adminFeedback(state,failure(error));state.root.querySelector('[data-subscription-admin-list]').innerHTML='<p class="subscription-empty">列表未能读取，请点击刷新列表。</p>';state.root.querySelector('[data-subscription-admin-pages]').hidden=true;}}
    finally{if(live(state)&&seq===state.readSequence){state.loading=false;state.root.querySelector('[data-subscription-admin-list]').setAttribute('aria-busy','false');}}
  }
  function reviewDialog(state,button) {
    const row=state.records.find(item=>item.id===button.dataset.subscriptionRecord),action=button.dataset.subscriptionReview;
    if(!row||!['approve','reject'].includes(action)||!['pending','uncertain'].includes(row.status)||(row.status==='uncertain'&&action!=='approve'))return;
    const reconcile=row.status==='uncertain',approve=action==='approve';
    const overlay=dialog(state,`<span class="subscription-kicker">SUBSCRIPTION REVIEW</span><h2 id="subscription-dialog-title">${reconcile?'核对订阅结果':approve?'通过这部影片的申请？':'不通过申请'}</h2><p class="subscription-dialog-title">${escape(row.title)}</p><p class="subscription-dialog-help">${reconcile?'只查询 NextFind 是否已经存在该订阅，不会再次创建任务。':approve?'确认后会在 NextFind 加入追更，同一影片的申请将共用此任务。':'填写审核说明，申请人可在本站查看。不会创建追更任务。'}</p><form data-subscription-review-form>${!approve?'<label class="subscription-note-label">审核说明 <textarea name="note" maxlength="300" rows="3" required placeholder="请简要说明原因"></textarea></label>':''}<p class="subscription-form-feedback" role="status"></p><div class="subscription-dialog-actions"><button type="button" class="btn btn-outline" data-subscription-close>取消</button><button type="submit" class="btn btn-primary">${reconcile?'核对现有任务':approve?'确认通过并订阅':'确认不通过'}</button></div></form>`,button);
    overlay.querySelector('form').addEventListener('submit',event=>{event.preventDefault();event.stopPropagation();void submitReview(state,row,action,event.currentTarget);});
  }
  async function submitReview(state,row,action,form) {
    if(state.mutating||!live(state))return;
    const note=action==='reject'?text(form.elements.note.value,300):'';if(action==='reject'&&!note)return;
    state.mutating=true;const button=form.querySelector('[type="submit"]'),feedback=form.querySelector('.subscription-form-feedback');button.disabled=true;feedback.textContent='正在处理…';
    try {const data=await request(state,`/api/admin/subscription-requests/${row.id}/${action}`,action==='reject'?{note}:{});if(!live(state)||!form.isConnected)return;const result=record(data.request);feedback.textContent=result?.status==='approved'?'审核完成，影片已加入追更。':result?.status==='rejected'?'已记录审核结果。':'处理结果待核查，请刷新列表确认。';button.textContent='已处理';adminFeedback(state,'处理结果已更新。');void loadAdmin(state);}
    catch(error){if(live(state)&&form.isConnected){feedback.textContent=failure(error,true);button.textContent='请核对列表';adminFeedback(state,'处理结果尚待核对，已重新读取列表。');void loadAdmin(state);}}
    finally{state.mutating=false;}
  }
  function mountAdmin(root) {
    stopAdmin();if(!root?.querySelector('[data-subscription-admin-list]'))return;
    const state={root,controllers:new Set(),records:[],readSequence:0,filter:'pending',page:1,hasMore:false,loading:false,stopped:false,mutating:false};adminState=state;
    const click=event=>{if(!(event.target instanceof Element))return;const button=event.target.closest('[data-subscription-admin],[data-subscription-filter],[data-subscription-review]');if(!button||!root.contains(button)||button.disabled)return;event.preventDefault();if(button.dataset.subscriptionReview){reviewDialog(state,button);return;}if(button.dataset.subscriptionFilter){if(!['pending','approved','rejected','uncertain','all'].includes(button.dataset.subscriptionFilter))return;state.filter=button.dataset.subscriptionFilter;state.page=1;}else{const action=button.dataset.subscriptionAdmin;if(action==='prev'){if(state.loading||state.page<=1)return;state.page--;}else if(action==='next'){if(state.loading||!state.hasMore||state.page>=1000)return;state.page++;}else if(action!=='refresh')return;}adminFeedback(state,'');void loadAdmin(state);};
    root.addEventListener('click',click);state.cleanup=()=>root.removeEventListener('click',click);void loadAdmin(state);
  }
  function stop(state) {if(!state)return;state.stopped=true;state.cleanup?.();for(const controller of state.controllers)controller.abort();state.controllers.clear();closeDialog(state);}
  function stopPublic(){if(publicState){stop(publicState);customerAdapter()?.stop?.();}publicState=null;}
  function stopAdmin(){stop(adminState);adminState=null;}
  window.MediaDockSubscriptions=Object.freeze({cardAction,publicMarkup,mountPublic,adminMarkup,mountAdmin,stopPublic,stopAdmin});
}());
