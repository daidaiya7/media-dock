/* Shared delivery workspace. Uses the existing order ledger and guarded actions. */
'use strict';
const deliveryStages = [
  ['all','全部进度',[]],
  ['payment','待核款',['pending_review','pending_payment','payment_reported']],
  ['issue','待生成',['eligible','approved']],
  ['processing','生成中',['processing','issuing']],
  ['issued','已生成',['issued']],
  ['review','需核查',['needs_review','failed']],
];
const deliveryChannels = [['all','全部渠道'],['web','网页'],['xianyu','闲鱼'],['xiaohongshu','小红书'],['manual','手工']];
function deliveryOrders({stage = state.deliveryStage, search = state.deliverySearch} = {}) {
  const statuses = deliveryStages.find(([id])=>id===stage)?.[2] || [];
  const needle = search.trim().toLowerCase();
  return state.overview.orders.filter(order =>
    (state.deliverySource==='all' || normalizedOrderSource(order)===state.deliverySource)
    && (!statuses.length || statuses.includes(order.status))
    && (!needle || [oid(order),order.buyer_note,orderProduct(order)?.name,orderSourceLabel(order)].join(' ').toLowerCase().includes(needle)));
}
function deliveryActionLabel(order) {
  if(['pending_review','pending_payment','payment_reported'].includes(order.status)) return '核对付款';
  if(['eligible','approved'].includes(order.status)) return '生成卡密';
  if(order.status==='issued') return '查看交付';
  if(['processing','issuing'].includes(order.status)) return '查看进度';
  return '核查订单';
}
function deliveryQueue() {
  const orders = deliveryOrders();
  if(!orders.length) return `<div class="delivery-empty">${icon('box',30)}<h2>当前没有${state.deliveryStage==='all'?'':deliveryStages.find(([id])=>id===state.deliveryStage)?.[1]||''}订单</h2><p>${state.deliverySearch?'试试其他订单号、套餐或买家备注。':'订单会按购买渠道汇入这里，也可以登记已发生的交易。'}</p><button class="btn btn-outline" data-action="new-order" data-source="${state.deliverySource==='all'?'manual':state.deliverySource}">${icon('plus',15)}登记订单</button></div>`;
  return `<div class="table-scroll"><table class="data-table delivery-table"><thead><tr><th>订单与套餐</th><th>渠道 / 买家</th><th>发卡进度</th><th>交付方式</th><th>操作</th></tr></thead><tbody>${orders.map(order=>`<tr><td><strong>${esc(productTitle(orderProduct(order)||{name:order.product_id}))}</strong><span class="order-meta">${esc(oid(order))}</span><span class="order-meta">${esc(friendlyDate(order.created_at))}</span><span class="delivery-source delivery-mobile-source">${esc(orderSourceLabel(order))}</span></td><td><span class="delivery-source">${esc(orderSourceLabel(order))}</span><span class="order-meta">${esc(order.buyer_note||'—')}</span></td><td>${badge(order.status)}</td><td>${order.delivery_mode==='claim'?'领取链接':'卡密内容'}</td><td><button class="btn btn-outline btn-small" data-action="order-detail" data-id="${esc(oid(order))}">${deliveryActionLabel(order)} ${icon('arrow',12)}</button></td></tr>`).join('')}</tbody></table></div>`;
}
function deliveryPage() {
  const total = orderSourceCounts();
  const source = state.deliverySource==='all'?'manual':state.deliverySource;
  const counts = deliveryStages.map(([id,label])=>[id,label,deliveryOrders({stage:id,search:''}).length]);
  return `<section class="delivery-page">${pageTitle('发卡','网页、闲鱼、小红书，共用同一套套餐与发卡流程。',`<button class="btn btn-primary" data-action="new-order" data-source="${source}">${icon('plus',16)}登记订单</button>`)}<div class="delivery-view-tabs" role="group" aria-label="发卡视图">${[['queue','发卡工作台'],['settings','套餐与渠道']].map(([id,label])=>`<button data-action="delivery-view" data-view="${id}" class="${state.deliveryView===id?'active':''}" aria-pressed="${state.deliveryView===id}">${label}</button>`).join('')}</div><div class="delivery-channels" role="group" aria-label="发卡渠道">${deliveryChannels.map(([id,label])=>`<button data-action="delivery-channel" data-source="${id}" class="${state.deliverySource===id?'active':''}" aria-pressed="${state.deliverySource===id}">${label}<span>${total[id]}</span></button>`).join('')}</div>${state.deliveryView==='settings'?deliverySettings():`<div class="delivery-stages" role="group" aria-label="发卡进度">${counts.map(([id,label,count])=>`<button data-action="delivery-stage" data-stage="${id}" class="${state.deliveryStage===id?'active':''}" aria-pressed="${state.deliveryStage===id}"><span>${label}</span><strong>${count}</strong></button>`).join('')}</div><section class="panel"><div class="filter-bar"><label class="search-input">${icon('search',16)}<span class="visually-hidden">搜索发卡订单</span><input class="input" id="delivery-search" placeholder="订单号、套餐或买家备注" value="${esc(state.deliverySearch)}"></label><button class="btn btn-outline btn-small" data-action="refresh-admin" aria-label="刷新发卡订单">${icon('refresh',15)}刷新</button></div><div id="delivery-queue">${deliveryQueue()}</div></section><p class="delivery-note">“已生成”表示卡密已准备好。网页领取与聊天发送的结果需分别核实；同一笔订单始终使用已有卡密。</p>`}</section>`;
}
function deliverySettings() {
  const source = state.deliverySource, settings = state.overview.settings || {};
  const channels = [
    ['web','网页购卡','买家提交订单 → 人工核款 → 生成卡密 → 网页领取',settings.payment_wechat_url||settings.payment_alipay_url?'收款码已设置':'待设置收款码'],
    ['xianyu','闲鱼发卡','核验平台已付款订单 → 匹配商品规格 → 生成并发送卡密','按商品规格匹配'],
    ['xiaohongshu','小红书发卡','登记交易 → 人工核款 → 生成卡密 → 私信交付','人工核款与发送'],
  ];
  const catalog = `<section class="panel"><div class="panel-head"><div><h2>共用套餐 · NextEmby</h2><p>各渠道按同一套餐生成兑换卡，每笔订单 1 张。</p></div><button class="btn btn-outline btn-small" data-action="check-nextemby">${icon('refresh',14)}检查卡源</button></div>${mappingTable()}</section>`;
  if(source==='all') return `${catalog}<div class="delivery-channel-cards">${channels.map(([id,title,description,status])=>`<section class="panel"><div class="panel-body"><span class="badge">${status}</span><h2>${title}</h2><p>${description}</p><button class="btn btn-outline btn-small" data-action="delivery-channel" data-source="${id}">管理渠道 ${icon('arrow',13)}</button></div></section>`).join('')}</div>`;
  if(source==='xianyu') return `${catalog}${xianyuDeliverySettings()}`;
  if(source==='web') return `${catalog}<section class="panel"><div class="panel-head"><h2>网页收款与领取</h2><span class="badge">${channels[0][3]}</span></div><div class="panel-body"><p class="help-text">买家提交订单后，在发卡工作台核对微信或支付宝实际到账，再生成卡密。已有领取凭证继续沿用。</p><div class="inline-actions mt-18"><a class="btn btn-secondary" href="/" target="_blank" rel="noopener">打开购卡网页 ${icon('external',14)}</a><button class="btn btn-outline" data-action="admin-tab" data-tab="settings">设置收款码与兑换地址</button><button class="btn btn-outline" data-action="delivery-view" data-view="queue">处理网页订单</button></div></div></section>`;
  if(source==='xiaohongshu') return `${catalog}<section class="panel"><div class="panel-head"><h2>小红书私信交付</h2><span class="badge">人工核款</span></div><div class="panel-body"><p class="help-text">在这里登记实际交易，核款和生成卡密使用同一发卡流程。复制订单中的交付内容后，前往小红书助手核对收件人并发送。</p><div class="inline-actions mt-18"><button class="btn btn-primary" data-action="new-order" data-source="xiaohongshu">${icon('plus',15)}登记小红书订单</button><button class="btn btn-outline" data-action="admin-tab" data-tab="xiaohongshu">打开小红书聊天</button></div><p class="micro mt-18">小红书店铺订单与自动发货尚未接入，聊天内容不会自动确认付款。</p></div></section>`;
  return `${catalog}<section class="panel"><div class="panel-body"><h2>其他交易手工发卡</h2><p class="help-text mt-12">登记交易、核对实际到账后，在同一订单生成卡密或领取链接。</p><button class="btn btn-primary mt-18" data-action="new-order" data-source="manual">登记手工订单</button></div></section>`;
}
function xianyuDeliverySettings() {
  const settings = state.overview.settings || {};
  return `<section class="panel"><div class="panel-head"><div><h2>闲鱼商品与规格</h2><p>将闲鱼规格对应到上方共用套餐。</p></div><button class="btn btn-outline btn-small" data-action="admin-tab" data-tab="assistant">账号与自动化规则 ${icon('arrow',13)}</button></div>${bindingForms()}</section><details class="panel delivery-advanced"><summary>卖家绑定与连接器配置</summary><div class="panel-body"><form id="xianyu-account-form"><label class="field"><span>闲鱼卖家账号 ID</span><input name="xianyu_account_id" value="${esc(settings.xianyu_account_id||'')}" maxlength="100" required placeholder="填写工作台读取的卖家账号 ID"><small>用于核对订单归属，请勿填写昵称。</small></label><p class="form-error" role="alert"></p><button type="submit" class="btn btn-secondary btn-small">保存账号绑定</button></form><div class="mt-24"><button class="btn btn-outline btn-small" data-action="bridge-config">查看连接器配置</button><div id="bridge-config-content"></div></div></div></details>`;
}
function selectDelivery(action, value) {
  if(action==='delivery-view' && ['queue','settings'].includes(value)) state.deliveryView=value;
  else if(action==='delivery-channel' && deliveryChannels.some(([id])=>id===value)) state.deliverySource=value;
  else if(action==='delivery-stage' && deliveryStages.some(([id])=>id===value)) state.deliveryStage=value;
  else return;
  state.tab='delivery'; renderAdminTab();
}
document.addEventListener('input', event => {
  if(event.target.id!=='delivery-search') return;
  state.deliverySearch=event.target.value;
  document.getElementById('delivery-queue').innerHTML=deliveryQueue();
});
document.addEventListener('change', event => {
  if(event.target.name==='source' && event.target.form?.id==='admin-order-form') updateManualOrderHint(event.target.form);
});
