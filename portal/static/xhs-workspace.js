/* 小红书配置与统计。只调用同源固定管理接口；现有聊天和发送保护由 app.js 保持。 */
'use strict';
const xhsToolViews=[['dashboard','grid','仪表盘'],['chat','chat','会话'],['ai','film','AI 聊天'],['rules','order','自动化规则'],['connection','settings','连接设置']];
const xhsTools={view:'chat',config:null,dashboard:null,loading:false,busy:false,promise:null,revision:0,version:0,error:'',notice:'',editingRule:null,reviewRun:null,preview:'',aiPreview:''};
const xhsToolEndpoint='/api/admin/xiaohongshu';
async function xhsToolCall(path,options={}) {
  try {return await api(path,options);}
  catch(error){if(error.message?.includes('请求等待超时'))error.message='请求超时，配置或测试结果尚未确认，请读取最新状态后再操作。';throw error;}
}
function xhsToolNavigation() {
  return `<div class="xhs-tool-tabs" role="group" aria-label="小红书工作区">${xhsToolViews.map(([id,ico,label])=>`<button type="button" data-xhs-view="${id}" aria-pressed="${xhsTools.view===id}">${icon(ico,15)}${label}</button>`).join('')}</div><div id="xhs-tools-feedback" role="status" aria-live="polite"></div><section id="xhs-tools" class="xhs-tools" hidden></section>`;
}
function resetXhsTools() {
  xhsTools.revision++; xhsTools.version++;
  Object.assign(xhsTools,{view:'chat',config:null,dashboard:null,loading:false,busy:false,promise:null,error:'',notice:'',editingRule:null,reviewRun:null,preview:'',aiPreview:''});
  document.querySelectorAll('#xhs-tools input[type="password"]').forEach(input=>{input.value='';});
  document.getElementById('xhs-tools')?.replaceChildren();
}
function xhsPublicConfig(value) {
  if(!value || typeof value.enabled!=='boolean' || !value.ai || !Array.isArray(value.rules) || value.rules.length>30 || !value.fallback) throw new Error('小红书配置格式不正确，请重新读取。');
  // 仅保存已知非敏感字段；即使上游误带凭据，也不进入前端配置或模板。
  return {enabled:value.enabled,account_id:String(value.account_id||''),poll_interval_seconds:value.poll_interval_seconds,min_reply_interval_seconds:value.min_reply_interval_seconds,max_replies_hour:value.max_replies_hour,manual_pause_seconds:value.manual_pause_seconds,
    ai:{enabled:value.ai.enabled===true,base_url:String(value.ai.base_url||''),model:String(value.ai.model||''),system_prompt:String(value.ai.system_prompt||''),auth_configured:value.ai.auth_configured===true},
    rules:value.rules.map(rule=>({id:String(rule.id||''),name:String(rule.name||''),enabled:rule.enabled===true,keywords:Array.isArray(rule.keywords)?rule.keywords.map(String):[],match_mode:rule.match_mode,reply_type:rule.reply_type,reply_text:String(rule.reply_text||''),priority:rule.priority})),
    fallback:{enabled:value.fallback.enabled===true,reply_type:value.fallback.reply_type,reply_text:String(value.fallback.reply_text||'')},runtime:value.runtime||{}};
}
function xhsConfigPayload(config=xhsTools.config) {
  if(!config) throw new Error('请先读取小红书配置。');
  return {enabled:config.enabled,account_id:config.account_id,poll_interval_seconds:config.poll_interval_seconds,min_reply_interval_seconds:config.min_reply_interval_seconds,max_replies_hour:config.max_replies_hour,manual_pause_seconds:config.manual_pause_seconds,
    ai:{enabled:config.ai.enabled,base_url:config.ai.base_url,model:config.ai.model,system_prompt:config.ai.system_prompt},
    rules:config.rules.map(rule=>({...rule,keywords:[...rule.keywords]})),fallback:{...config.fallback}};
}
function xhsPublicDashboard(value) {
  if(!value||!value.counts||!Array.isArray(value.recent))throw new Error('运行统计格式不正确，请重新读取。');
  const rows=items=>(Array.isArray(items)?items:[]).slice(0,200).map(item=>({id:String(item.id||''),at:item.at,account_id:String(item.account_id||''),conversation_id:String(item.conversation_id||''),rule_id:String(item.rule_id||''),rule_name:String(item.rule_name||''),status:String(item.status||''),reason:String(item.reason||''),request_id:String(item.request_id||'')}));
  const counts={};for(const key of ['observed','matched','sent','failed','needs_review','skipped','ai_calls'])if(Number.isInteger(value.counts[key])&&value.counts[key]>=0)counts[key]=value.counts[key];
  return {enabled:value.enabled===true,account_id:String(value.account_id||''),runtime:value.runtime||{},counts,recent:rows(value.recent),unresolved:rows(value.unresolved),unresolved_total:Number.isInteger(value.unresolved_total)?value.unresolved_total:rows(value.unresolved).length};
}
async function loadXhsTools(force=false) {
  if(state.tab!=='xiaohongshu' || !state.auth?.authenticated || xhsTools.busy) return;
  if(xhsTools.promise) return xhsTools.promise;
  if(xhsTools.config && !force) return;
  const revision=xhsTools.revision;
  xhsTools.loading=true; xhsTools.error=''; renderXhsTools();
  const work=(async()=>{
    try {
      const [config,dashboard]=await Promise.all([xhsToolCall(`${xhsToolEndpoint}/automation`),xhsToolCall(`${xhsToolEndpoint}/dashboard`)]);
      if(revision!==xhsTools.revision) return;
      xhsTools.config=xhsPublicConfig(config); xhsTools.dashboard=xhsPublicDashboard(dashboard); xhsTools.version++;
    } catch(error) { if(revision===xhsTools.revision)xhsTools.error=errorText(error); }
    finally { if(revision===xhsTools.revision){xhsTools.loading=false; renderXhsTools();} }
  })();
  xhsTools.promise=work;
  try { await work; } finally { if(xhsTools.promise===work)xhsTools.promise=null; }
}
function xhsToolRuntime(runtime={}) {
  const labels={disabled:'自动回复已关闭',idle:'等待新消息',running:'正在处理',warming_up:'正在建立消息基线',waiting_account:'等待绑定账号就绪',ready:'运行中',polling:'正在读取会话',paused:'人工操作暂停中',needs_attention:'需要处理',blocked:'自动回复已暂停',error:'执行异常',stopped:'已停止',offline:'服务离线',unconfigured:'尚未配置'};
  return labels[runtime.state]||'运行状态待确认';
}
function xhsToolReason(reason) {
  const labels={ai_preview:'AI 测试（未发送）',no_rule:'未匹配规则',configuration_or_manual_change:'配置或人工操作已变化',conversation_changed:'会话已变化',send_confirmed:'已取得发送确认',send_not_confirmed:'未取得发送确认',send_result_unknown:'发送结果不明，待核查',process_interrupted:'执行中断，待核查',manually_resolved:'已记录人工核查结果',disabled:'自动回复关闭',manual_activity:'人工操作后暂停',account_not_ready:'绑定账号尚未就绪',no_c2c_conversations:'暂无可处理的私信会话',worker_or_storage_unavailable:'连接或记录服务暂不可用',login_required:'等待账号登录',account_mismatch:'账号不匹配',account_changed:'账号已变化',manual_pause:'人工操作后暂停',cooldown:'冷却间隔内',rate_limited:'达到频率上限',hourly_limit:'达到每小时上限',unresolved_send:'发送结果待人工核查',needs_review:'发送结果待核查',no_match:'未匹配规则',ai_disabled:'AI 未启用',ai_unconfigured:'AI 尚未配置',ai_failed:'AI 生成失败',rule_matched:'命中规则',matched:'命中规则',sent:'已取得发送确认',failed:'执行失败',skipped:'已跳过',baseline:'已有消息不自动回复',platform_network_restricted:'平台限制当前网络'};
  return labels[reason]||'请核对运行记录';
}
function renderXhsTools() {
  const target=document.getElementById('xhs-tools'); if(!target)return;
  document.querySelectorAll('[data-xhs-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.xhsView===xhsTools.view)));
  const feedback=document.getElementById('xhs-tools-feedback');
  feedback.innerHTML=xhsTools.error?`<div class="callout danger xhs-tool-alert" role="alert">${esc(xhsTools.error)}</div>`:xhsTools.notice?`<div class="callout xhs-tool-alert">${esc(xhsTools.notice)}</div>`:'';
  target.hidden=xhsTools.view==='chat';
  const chat=document.getElementById('xhs-chat'); if(chat)chat.hidden=xhsTools.view!=='chat'||!xhsIsReady();
  const login=document.getElementById('xhs-login'); if(login)login.hidden=!(xhsWorkspace.status?.browser_running&&!xhsIsReady()&&['chat','connection'].includes(xhsTools.view));
  if(target.hidden)return;
  const statusVersion=xhsTools.view==='connection'?JSON.stringify([xhsWorkspace.status?.state,xhsWorkspace.accountId,xhsWorkspace.status?.detail_code]):'';
  const key=[xhsTools.view,xhsTools.version,xhsTools.editingRule,xhsTools.reviewRun,xhsTools.loading,statusVersion].join(':');
  if(target.dataset.renderKey!==key) {
    target.dataset.renderKey=key;
    if(xhsTools.view==='connection')target.innerHTML=xhsConnectionView();
    else if(!xhsTools.config)target.innerHTML=`<section class="panel"><div class="xhs-tool-empty">${xhsTools.loading?'正在读取配置与统计…':'配置尚未读取。'}${!xhsTools.loading?'<div class="mt-18"><button class="btn btn-outline btn-small" data-xhs-tool="refresh">重新读取</button></div>':''}</div></section>`;
    else target.innerHTML=({dashboard:xhsDashboardView,ai:xhsAIView,rules:xhsRulesView}[xhsTools.view]||xhsDashboardView)();
  }
  target.querySelectorAll('[data-xhs-control]').forEach(control=>{control.disabled=xhsTools.busy||control.dataset.xhsBlocked==='true';});
  const aiTest=target.querySelector('#xhs-ai-test-button');
  if(aiTest)aiTest.disabled=xhsTools.busy||!target.querySelector('#xhs-ai-test-confirmed')?.checked||!xhsTools.config?.ai.enabled||!xhsTools.config?.ai.auth_configured;
  const run=xhsTools.dashboard?.unresolved.find(item=>item.id===xhsTools.reviewRun);
  target.querySelectorAll('[data-xhs-tool="resolve-run"]').forEach(button=>{button.disabled=xhsTools.busy||!target.querySelector('#xhs-run-reviewed')?.checked||!run||run.account_id!==xhsWorkspace.accountId||!xhsIsReady();});
}
function xhsToolRefreshButton(){return '<button class="btn btn-outline btn-small" data-xhs-tool="refresh" data-xhs-control>读取最新状态</button>';}
function xhsRunReviewView() {
  const unresolved=xhsTools.dashboard?.unresolved||[],total=xhsTools.dashboard?.unresolved_total||unresolved.length;
  if(!unresolved.length)return '';
  const run=unresolved.find(item=>item.id===xhsTools.reviewRun), matching=run?.account_id===xhsWorkspace.accountId&&xhsIsReady();
  return `<section class="panel xhs-unresolved-panel"><div class="panel-head"><div><h2>待人工核查 · ${total}</h2><p>这些发送结果尚未确认；只记录核查结论，不会重发。</p></div></div>${run?`<div class="panel-body xhs-run-review"><h3>${esc(run.rule_name||'自动回复')} · ${esc(friendlyDate(run.at))}</h3><p class="xhs-tool-account-id">账号 ${esc(run.account_id)}<br>会话 ${esc(run.conversation_id)}<br>请求 ${esc(run.request_id)}</p><p>${matching?'请在小红书原会话核对这次消息的实际结果。':'请先连接记录所属账号，再核对原会话。'}</p><label class="checkbox-label mt-18"><input id="xhs-run-reviewed" type="checkbox">我已核对原聊天、收件人和本次发送结果</label><div class="inline-actions mt-18"><button type="button" class="btn btn-outline btn-small" data-xhs-tool="resolve-run" data-resolution="sent" disabled>已核实发送成功</button><button type="button" class="btn btn-outline btn-small" data-xhs-tool="resolve-run" data-resolution="not_sent" disabled>已核实未发送</button><button type="button" class="btn btn-outline btn-small" data-xhs-tool="close-review">取消</button></div><p class="micro mt-12">仅更新核查记录，不重新生成回复、不重新发送，也不改变订单状态。</p></div>`:''}<div class="table-scroll xhs-unresolved-list"><table class="xhs-tool-log"><thead><tr><th>时间 / 会话</th><th>规则 / 账号</th><th>操作</th></tr></thead><tbody>${unresolved.map(item=>`<tr><td>${esc(friendlyDate(item.at))}<small>${esc(item.conversation_id)}</small></td><td>${esc(item.rule_name||'自动回复')}<small>${esc(item.account_id)}</small></td><td><button type="button" class="btn btn-outline btn-small" data-xhs-tool="review-run" data-run-id="${esc(item.id)}" data-xhs-control>核查结果</button></td></tr>`).join('')}</tbody></table></div>${total>unresolved.length?'<div class="xhs-tool-empty">先处理当前最早的 200 条待办，再刷新加载余下记录。</div>':''}</section>`;
}
function xhsDashboardView() {
  const dashboard=xhsTools.dashboard, config=xhsTools.config, runtime=dashboard?.runtime||config.runtime, counts=dashboard?.counts||{};
  const metrics=[['observed','已观察消息','去重消息，含初始化历史基线'],['matched','命中规则','按已保存规则匹配'],['sent','发送已确认','不代表对方已读'],['needs_review','待人工核查','未确认结果不会自动重发'],['failed','执行失败','请查看下方记录'],['skipped','已跳过','含冷却与频率限制'],['ai_calls','AI 调用','以服务端记录为准']];
  const recent=Array.isArray(dashboard?.recent)?dashboard.recent.slice(0,100):[];
  return `<div class="xhs-tool-heading"><div><h2>私信仪表盘</h2><p>自动化运行记录与账号状态。</p></div>${xhsToolRefreshButton()}</div><div class="xhs-tool-state"><div><strong>${esc(xhsToolRuntime(runtime))}</strong><span class="badge ${config.enabled?'green':'gray'}">${config.enabled?'总开关已开启':'总开关已关闭'}</span></div><p>${runtime?.detail_code?esc(xhsToolReason(runtime.detail_code)):'统计来自实际执行记录；尚未产生记录时不会显示测试数据。'}${runtime?.last_poll_at?` · 最近读取 ${esc(friendlyDate(runtime.last_poll_at))}`:''}${runtime?.paused_until?` · 暂停至 ${esc(friendlyDate(runtime.paused_until))}`:''}</p></div><div class="xhs-metrics">${metrics.map(([key,label,hint])=>`<div class="xhs-metric"><span>${label}</span><strong>${Number.isInteger(counts[key])&&counts[key]>=0?counts[key]:'—'}</strong><small>${hint}</small></div>`).join('')}</div>${xhsRunReviewView()}<section class="panel"><div class="panel-head"><div><h2>最近执行记录</h2><p>展示结果与原因，不显示私信正文或凭据。</p></div><button class="btn btn-outline btn-small" data-xhs-view="rules">管理规则</button></div>${recent.length?`<div class="table-scroll"><table class="xhs-tool-log"><thead><tr><th>时间 / 会话</th><th>规则</th><th>结果</th><th>说明</th></tr></thead><tbody>${recent.map(item=>`<tr><td>${esc(friendlyDate(item.at))}<small>${esc(item.conversation_id||'—')}</small></td><td>${esc(item.rule_name||'未命名规则')}</td><td>${esc(({draft:'测试草稿',processing:'处理中',observed:'已观察',matched:'已匹配',sent:'发送已确认',failed:'失败',needs_review:'待核查',skipped:'已跳过'})[item.status]||'待确认')}</td><td>${esc(xhsToolReason(item.reason))}${item.request_id?`<small>请求 ${esc(item.request_id)}</small>`:''}</td></tr>`).join('')}</tbody></table></div>`:'<div class="xhs-tool-empty">暂无执行记录。配置并开启自动化后，这里显示真实运行结果。</div>'}</section>`;
}
function xhsAIView() {
  const ai=xhsTools.config.ai;
  return `<div class="xhs-tool-heading"><div><h2>AI 聊天配置</h2><p>使用 OpenAI 兼容接口生成回复，规则决定何时使用 AI。</p></div>${xhsToolRefreshButton()}</div><div class="xhs-tools-grid"><form id="xhs-ai-config-form" class="panel xhs-tool-form"><div class="panel-head"><h2>模型与回复风格</h2><span class="badge ${ai.auth_configured?'green':'gray'}">${ai.auth_configured?'密钥已配置':'待配置密钥'}</span></div><div class="panel-body"><fieldset data-xhs-control><label class="checkbox-label"><input type="checkbox" name="enabled" ${ai.enabled?'checked':''}>启用 AI 回复能力</label><label class="field"><span>接口地址（HTTPS）</span><input type="url" name="base_url" value="${esc(ai.base_url)}" maxlength="300" placeholder="https://你的服务商/v1" required><small>填写 OpenAI 兼容 API 基础地址；连接由服务器发起。</small></label><label class="field"><span>模型名称</span><input name="model" value="${esc(ai.model)}" maxlength="100" placeholder="服务商提供的模型名称" required></label><label class="field"><span>API 密钥</span><input type="password" name="api_key" autocomplete="new-password" maxlength="2048" placeholder="${ai.auth_configured?'已保存，留空保留':'填写服务商密钥'}"><small>已有密钥不会回显；留空保留当前值。更换服务商时请同时填写新密钥。</small></label>${ai.auth_configured?'<label class="checkbox-label"><input type="checkbox" name="clear_key">清除已保存的密钥</label>':''}<label class="field"><span>系统提示词</span><textarea name="system_prompt" rows="7" maxlength="4000" placeholder="说明服务范围、回复语气及需要人工处理的情况">${esc(ai.system_prompt)}</textarea></label><button class="btn btn-primary" type="submit">保存 AI 配置</button><p class="micro mt-12">保存模型配置不会自动开启私信回复；请在自动化规则中设置总开关与匹配规则。</p></fieldset></div></form><section class="panel"><div class="panel-head"><h2>测试 AI 回复</h2></div><div class="panel-body"><p>使用已保存的配置生成测试内容。可能产生模型费用，结果仅显示在这里，不发送到小红书。</p><form id="xhs-ai-preview-form" class="xhs-tool-form mt-18"><fieldset data-xhs-control><label class="field"><span>测试消息</span><textarea name="text" maxlength="1000" rows="4" required placeholder="输入一条示例咨询"></textarea></label><label class="checkbox-label"><input id="xhs-ai-test-confirmed" type="checkbox">确认调用已配置的 AI（可能计费）</label><button id="xhs-ai-test-button" class="btn btn-outline" type="submit" disabled>生成测试回复</button></fieldset></form><div id="xhs-ai-preview-result" class="xhs-tool-result" ${xhsTools.aiPreview?'':'hidden'}>${esc(xhsTools.aiPreview)}</div></div></section></div>`;
}
function xhsRuleEditor() {
  if(!xhsTools.editingRule)return '';
  const current=xhsTools.config.rules.find(rule=>rule.id===xhsTools.editingRule)||{id:'',name:'',enabled:false,keywords:[],match_mode:'contains',reply_type:'fixed',reply_text:'',priority:0};
  return `<form id="xhs-rule-form" class="panel xhs-rule-editor xhs-tool-form"><div class="panel-head"><h2>${current.id?'编辑规则':'新增规则'}</h2><button class="btn btn-outline btn-small" type="button" data-xhs-tool="cancel-rule" data-xhs-control>取消</button></div><div class="panel-body"><fieldset data-xhs-control><div class="two-col"><label class="field"><span>规则名称</span><input name="name" maxlength="80" value="${esc(current.name)}" required></label><label class="field"><span>优先级（数字小的优先）</span><input type="number" name="priority" min="0" max="1000" value="${esc(current.priority)}" required></label></div><label class="field"><span>关键词（最多 20 个，每个不超过 100 字）</span><textarea name="keywords" maxlength="2019" rows="3" required>${esc(current.keywords.join('\n'))}</textarea></label><div class="two-col"><label class="field"><span>匹配方式</span><select name="match_mode"><option value="contains" ${current.match_mode==='contains'?'selected':''}>包含任一关键词</option><option value="exact" ${current.match_mode==='exact'?'selected':''}>与任一关键词完全一致</option></select></label><label class="field"><span>回复方式</span><select name="reply_type"><option value="fixed" ${current.reply_type==='fixed'?'selected':''}>固定文本</option><option value="ai" ${current.reply_type==='ai'?'selected':''}>AI 生成</option></select></label></div><label class="field" id="xhs-rule-reply-field" ${current.reply_type==='ai'?'hidden':''}><span>固定回复</span><textarea name="reply_text" rows="4" maxlength="1000">${esc(current.reply_text)}</textarea></label><label class="checkbox-label"><input name="enabled" type="checkbox" ${current.enabled?'checked':''}>启用这条规则（仍受自动化总开关控制）</label><button class="btn btn-primary" type="submit">保存规则</button></fieldset></div></form>`;
}
function xhsRulesView() {
  const config=xhsTools.config, rules=[...config.rules].sort((a,b)=>a.priority-b.priority), account=config.account_id||xhsWorkspace.accountId;
  return `<div class="xhs-tool-heading"><div><h2>自动化规则</h2><p>先保存规则，再按需开启总开关；同一条新消息按优先级匹配。</p></div><button class="btn btn-primary btn-small" data-xhs-tool="new-rule" data-xhs-control data-xhs-blocked="${rules.length>=30}" ${rules.length>=30?'disabled':''}>${icon('plus',14)}新增规则</button></div>${xhsRuleEditor()}<div class="xhs-tools-grid"><div><section class="panel"><div class="panel-head"><h2>关键词规则 · ${rules.length} / 30</h2>${xhsToolRefreshButton()}</div><div class="panel-body">${rules.length?`<div class="xhs-rule-list">${rules.map(rule=>`<article class="xhs-rule"><div><h3>${esc(rule.name)}<span class="badge ${rule.enabled?'green':'gray'}">${rule.enabled?'已启用':'已停用'}</span></h3><p>优先级 ${esc(rule.priority)} · ${rule.match_mode==='exact'?'完全匹配':'包含关键词'} · ${rule.reply_type==='ai'?'AI 生成':'固定回复'}</p><p>${esc(rule.keywords.join(' / '))}</p>${rule.reply_type==='fixed'?`<p class="xhs-rule-text">${esc(rule.reply_text)}</p>`:''}</div><div class="xhs-rule-actions"><button class="btn btn-outline btn-small" data-xhs-tool="edit-rule" data-rule-id="${esc(rule.id)}" data-xhs-control>编辑</button><button class="btn btn-outline btn-small" data-xhs-tool="toggle-rule" data-rule-id="${esc(rule.id)}" data-xhs-control>${rule.enabled?'停用':'启用'}</button><button class="btn btn-outline btn-small" data-xhs-tool="delete-rule" data-rule-id="${esc(rule.id)}" data-xhs-control>删除</button></div></article>`).join('')}</div>`:'<div class="xhs-tool-empty">还没有规则。新增的规则默认停用，可先保存并测试匹配。</div>'}</div></section><section class="panel"><div class="panel-head"><h2>测试已保存规则</h2></div><div class="panel-body"><form id="xhs-rule-preview-form" class="xhs-tool-form"><fieldset data-xhs-control><label class="field"><span>测试消息</span><textarea name="text" rows="3" maxlength="1000" required placeholder="输入买家可能发送的消息"></textarea></label><button class="btn btn-outline" type="submit">查看匹配结果</button><p class="micro mt-12">只测试匹配，不调用 AI、不发送私信。</p></fieldset></form><div id="xhs-rule-preview-result" class="xhs-tool-result" ${xhsTools.preview?'':'hidden'}>${esc(xhsTools.preview)}</div></div></section></div><form id="xhs-automation-config-form" class="panel xhs-tool-form"><div class="panel-head"><h2>运行设置</h2><span class="badge ${config.enabled?'green':'gray'}">${config.enabled?'已开启':'已关闭'}</span></div><div class="panel-body"><fieldset data-xhs-control><label class="checkbox-label"><input name="enabled" type="checkbox" ${config.enabled?'checked':''}>开启自动回复</label><label class="field"><span>绑定的小红书账号 ID</span><input name="account_id" maxlength="100" value="${esc(account)}" pattern="[A-Za-z0-9_-]{1,100}" placeholder="连接账号后填写或使用当前账号"><small>仅处理此账号的新消息。账号不匹配时暂停。</small></label><div class="two-col"><label class="field"><span>读取间隔（秒）</span><input type="number" name="poll_interval_seconds" min="5" max="120" value="${esc(config.poll_interval_seconds)}" required></label><label class="field"><span>回复冷却（秒）</span><input type="number" name="min_reply_interval_seconds" min="30" max="3600" value="${esc(config.min_reply_interval_seconds)}" required></label><label class="field"><span>每小时最多回复</span><input type="number" name="max_replies_hour" min="1" max="60" value="${esc(config.max_replies_hour)}" required></label><label class="field"><span>人工操作后暂停（秒）</span><input type="number" name="manual_pause_seconds" min="60" max="3600" value="${esc(config.manual_pause_seconds)}" required></label></div><label class="checkbox-label"><input name="fallback_enabled" type="checkbox" ${config.fallback.enabled?'checked':''}>未匹配规则时使用兜底回复</label><label class="field"><span>兜底方式</span><select name="fallback_type"><option value="fixed" ${config.fallback.reply_type==='fixed'?'selected':''}>固定文本</option><option value="ai" ${config.fallback.reply_type==='ai'?'selected':''}>AI 生成</option></select></label><label class="field" id="xhs-fallback-reply-field" ${config.fallback.reply_type==='ai'?'hidden':''}><span>兜底文本</span><textarea name="fallback_text" rows="3" maxlength="1000">${esc(config.fallback.reply_text)}</textarea></label><button class="btn btn-primary" type="submit">保存运行设置</button><p class="micro mt-12">聊天不用于核验付款，规则不会生成卡密或修改订单。发送结果不明时需先人工核查。</p></fieldset></div></form></div>`;
}
function xhsConnectionView() {
  const status=xhsWorkspace.status, [label,color]=xhsStatusInfo();
  return `<div class="xhs-tool-heading"><div><h2>连接设置</h2><p>服务器独立浏览器与小红书专用网络配置。</p></div><button class="btn btn-outline btn-small" data-action="xhs-refresh">刷新连接状态</button></div><section class="panel"><div class="panel-body"><div class="xhs-tool-state"><div><span>网页私信</span><span class="badge ${color}">${esc(label)}</span></div><div><span>当前账号</span><span class="xhs-tool-account-id">${esc(xhsWorkspace.accountId||'尚未登录')}</span></div><p>${esc(xhsAccountHint(status,xhsIsReady()))}</p></div><div class="inline-actions"><button class="btn btn-outline" data-action="xhs-proxy">独立代理与出口</button>${status?.browser_running?`<button class="btn btn-outline" data-action="xhs-stop" ${xhsWorkspace.mutating?'disabled':''}>停止连接</button>`:`<button class="btn btn-primary" data-action="xhs-start" ${!status||status.configured===false||status.state==='offline'||xhsWorkspace.mutating?'disabled':''}>启动连接</button>`}${status?.browser_running&&!xhsIsReady()?'<button class="btn btn-outline" data-action="xhs-login-refresh">查看服务器页面</button>':''}</div><p class="micro mt-18">店铺客服与自动订单接入尚未启用。网页私信连接状态不代表消息已发送或订单已付款。</p></div></section>`;
}
async function selectXhsToolView(view) {
  if(!xhsToolViews.some(([id])=>id===view))return;
  if(xhsTools.view==='chat'&&view!=='chat'&&!xhsWorkspace.mutating) {
    // 离开会话页后废弃其读取结果；设置/仪表盘不应延长人工接管暂停。
    xhsWorkspace.revision++; xhsWorkspace.messageData=null; xhsWorkspace.messagePhase='idle';
  }
  // 密钥不跨视图暂存；已保存的密钥始终只在服务端。
  document.querySelectorAll('#xhs-tools input[type="password"]').forEach(input=>{input.value='';});
  xhsTools.view=view; xhsTools.error=''; xhsTools.notice=''; renderXhsTools();
  if(view==='chat')await resumeXhsWorkspace();
  else await loadXhsTools();
}
async function saveXhsToolConfig(payload,notice) {
  if(xhsTools.busy)return false;
  const revision=xhsTools.revision;
  xhsTools.busy=true; xhsTools.error=''; xhsTools.notice=''; renderXhsTools();
  try {
    const result=await xhsToolCall(`${xhsToolEndpoint}/automation`,{method:'POST',body:payload});
    if(revision!==xhsTools.revision)return false;
    xhsTools.config=xhsPublicConfig(result); xhsTools.version++; xhsTools.notice=notice; xhsTools.editingRule=null;
    return true;
  } catch(error) { if(revision===xhsTools.revision)xhsTools.error=errorText(error); return false; }
  finally { if(payload.ai)payload.ai.api_key=''; if(revision===xhsTools.revision){xhsTools.busy=false;renderXhsTools();} }
}
async function handleXhsToolAction(action,button) {
  if(xhsTools.busy)return;
  if(action==='refresh'){xhsTools.notice='';await loadXhsTools(true);return;}
  if(!xhsTools.config)return;
  if(action==='review-run'||action==='close-review') {
    const id=button?.dataset.runId;
    if(action==='review-run'&&!xhsTools.dashboard?.unresolved.some(item=>item.id===id&&item.status==='needs_review'))return;
    xhsTools.reviewRun=action==='close-review'?null:id;renderXhsTools();return;
  }
  if(action==='resolve-run')return resolveXhsAutomationRun(button?.dataset.resolution);
  if(action==='new-rule'||action==='edit-rule'||action==='cancel-rule') {
    if(action==='new-rule'&&xhsTools.config.rules.length>=30)return;
    const id=button?.dataset.ruleId;
    if(action==='edit-rule'&&!xhsTools.config.rules.some(rule=>rule.id===id))return;
    xhsTools.editingRule=action==='cancel-rule'?null:action==='new-rule'?'new':id; xhsTools.version++; renderXhsTools();
    if(action!=='cancel-rule')document.querySelector('#xhs-rule-form input[name="name"]')?.focus();
    return;
  }
  if(['toggle-rule','delete-rule'].includes(action)) {
    const payload=xhsConfigPayload(), index=payload.rules.findIndex(rule=>rule.id===button?.dataset.ruleId); if(index<0)return;
    if(action==='delete-rule'){if(!window.confirm('删除这条自动回复规则？'))return;payload.rules.splice(index,1);}
    else payload.rules[index].enabled=!payload.rules[index].enabled;
    await saveXhsToolConfig(payload,action==='delete-rule'?'规则已删除。':'规则状态已保存；是否执行仍由总开关和账号状态决定。');
  }
}
async function resolveXhsAutomationRun(resolution) {
  const run=xhsTools.dashboard?.unresolved.find(item=>item.id===xhsTools.reviewRun);
  if(xhsTools.busy||!run||!['sent','not_sent'].includes(resolution)||!document.getElementById('xhs-run-reviewed')?.checked||run.account_id!==xhsWorkspace.accountId||!xhsIsReady())return;
  if(!/^[A-Za-z0-9_-]{1,100}$/.test(run.id))throw new Error('核查记录编号不正确，请重新读取。');
  const revision=xhsTools.revision;let recorded=false;
  xhsTools.busy=true;xhsTools.error='';xhsTools.notice='';renderXhsTools();
  try {
    const result=await xhsToolCall(`${xhsToolEndpoint}/automation/runs/${encodeURIComponent(run.id)}/resolve`,{method:'POST',body:{resolution,confirmed:true}});
    if(revision!==xhsTools.revision)return;
    if(result.id!==run.id||result.status!==(resolution==='sent'?'sent':'failed')||result.reason!=='manually_resolved'||result.will_send!==false)throw new Error('核查结果尚未确认，请读取最新记录。');
    recorded=true;xhsTools.reviewRun=null;
    xhsTools.dashboard.unresolved=xhsTools.dashboard.unresolved.filter(item=>item.id!==run.id);
    xhsTools.dashboard.unresolved_total=Math.max(0,xhsTools.dashboard.unresolved_total-1);
    delete xhsTools.dashboard.counts.needs_review;
    xhsTools.notice='已记录人工核查结果，没有重新发送消息。';xhsTools.version++;
    const dashboard=await xhsToolCall(`${xhsToolEndpoint}/dashboard`);
    if(revision===xhsTools.revision){xhsTools.dashboard=xhsPublicDashboard(dashboard);xhsTools.version++;}
  }catch(error){if(revision===xhsTools.revision)xhsTools.error=recorded?'核查结果已保存，但统计暂未刷新，请读取最新状态。':errorText(error);}
  finally{if(revision===xhsTools.revision){xhsTools.busy=false;renderXhsTools();}}
}
async function submitXhsToolForm(form) {
  if(xhsTools.busy||!xhsTools.config)return;
  const fields=form.elements;
  xhsTools.error=''; xhsTools.notice='';
  if(form.id==='xhs-ai-config-form') {
    const payload=xhsConfigPayload();
    payload.ai={enabled:fields.enabled.checked,base_url:fields.base_url.value.trim(),model:fields.model.value.trim(),system_prompt:fields.system_prompt.value,api_key:fields.api_key.value,clear_key:fields.clear_key?.checked===true};
    fields.api_key.value='';
    await saveXhsToolConfig(payload,'AI 配置已保存。自动回复总开关保持原设置。');
  } else if(form.id==='xhs-automation-config-form') {
    const payload=xhsConfigPayload();
    payload.enabled=fields.enabled.checked; payload.account_id=fields.account_id.value.trim();
    for(const name of ['poll_interval_seconds','min_reply_interval_seconds','max_replies_hour','manual_pause_seconds'])payload[name]=Number(fields[name].value);
    payload.fallback={enabled:fields.fallback_enabled.checked,reply_type:fields.fallback_type.value,reply_text:fields.fallback_text.value};
    await saveXhsToolConfig(payload,'运行设置已保存，请在仪表盘查看实际执行状态。');
  } else if(form.id==='xhs-rule-form') {
    const payload=xhsConfigPayload(), existing=payload.rules.findIndex(rule=>rule.id===xhsTools.editingRule);
    const rule={id:existing>=0?payload.rules[existing].id:`rule_${crypto.randomUUID()}`,name:fields.name.value.trim(),enabled:fields.enabled.checked,keywords:[...new Set(fields.keywords.value.split(/[\n,，]/).map(value=>value.trim()).filter(Boolean))],match_mode:fields.match_mode.value,reply_type:fields.reply_type.value,reply_text:fields.reply_type.value==='fixed'?fields.reply_text.value:'',priority:Number(fields.priority.value)};
    if(!rule.keywords.length||rule.keywords.length>20||rule.keywords.some(word=>Array.from(word).length>100))throw new Error('请填写 1–20 个关键词，每个不超过 100 字。');
    if(existing>=0)payload.rules[existing]=rule;else payload.rules.push(rule);
    await saveXhsToolConfig(payload,'规则已保存。可用测试消息检查匹配结果。');
  } else if(['xhs-rule-preview-form','xhs-ai-preview-form'].includes(form.id)) {
    const ai=form.id==='xhs-ai-preview-form', text=fields.text.value.trim();
    if(!text || (ai&&(!fields['xhs-ai-test-confirmed']?.checked || !xhsTools.config.ai.enabled || !xhsTools.config.ai.auth_configured)))return;
    const revision=xhsTools.revision; xhsTools.busy=true;
    if(ai)xhsTools.aiPreview='';else xhsTools.preview='';
    const previous=document.getElementById(ai?'xhs-ai-preview-result':'xhs-rule-preview-result'); if(previous){previous.hidden=true;previous.textContent='';}
    renderXhsTools();
    try {
      const result=await xhsToolCall(`${xhsToolEndpoint}/automation/${ai?'ai-preview':'preview'}`,{method:'POST',body:ai?{text,confirmed:true}:{text}});
      if(revision!==xhsTools.revision)return;
      if(result.will_send!==false || result.ai_called!==ai)throw new Error('测试结果无法确认，请重新读取状态。');
      const response=ai?`${String(result.reply_text||'未生成回复。')}\n\n测试草稿 · 未发送到小红书。`:result.matched?`匹配规则：${result.rule_name||'未命名规则'}\n${result.requires_ai?'此规则使用 AI；本次仅匹配，未调用 AI。':result.reply_text||'无固定回复。'}\n未发送私信。`:'没有匹配的已保存规则。未发送私信。';
      if(ai)xhsTools.aiPreview=response;else xhsTools.preview=response;
      const output=document.getElementById(ai?'xhs-ai-preview-result':'xhs-rule-preview-result'); if(output){output.hidden=false;output.textContent=response;}
    } catch(error) { if(revision===xhsTools.revision)xhsTools.error=errorText(error); }
    finally {if(revision===xhsTools.revision){xhsTools.busy=false;renderXhsTools();}}
  }
}
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-xhs-view],[data-xhs-tool]'); if(!button)return;
  if(button.dataset.xhsView)void selectXhsToolView(button.dataset.xhsView);
  else void handleXhsToolAction(button.dataset.xhsTool,button).catch(error=>{xhsTools.error=errorText(error);renderXhsTools();});
});
document.addEventListener('submit',event=>{
  if(!['xhs-ai-config-form','xhs-automation-config-form','xhs-rule-form','xhs-rule-preview-form','xhs-ai-preview-form'].includes(event.target.id))return;
  event.preventDefault(); event.stopImmediatePropagation();
  void submitXhsToolForm(event.target).catch(error=>{xhsTools.error=errorText(error);renderXhsTools();});
},true);
document.addEventListener('change',event=>{
  if(event.target.id==='xhs-ai-test-confirmed')renderXhsTools();
  if(event.target.id==='xhs-run-reviewed')renderXhsTools();
  if(event.target.name==='reply_type'&&event.target.form?.id==='xhs-rule-form')document.getElementById('xhs-rule-reply-field').hidden=event.target.value==='ai';
  if(event.target.name==='fallback_type'&&event.target.form?.id==='xhs-automation-config-form')document.getElementById('xhs-fallback-reply-field').hidden=event.target.value==='ai';
});
