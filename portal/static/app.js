/* MediaDock portal. All account actions stay same-origin; public artwork is allowlisted. */
'use strict';
const app = document.getElementById('app');
const modalRoot = document.getElementById('modal-root');
const state = { catalog: null, overview: null, tab: 'overview', settingsCategory: 'site', filter: 'all', search: '', status: '', orderSource: 'all', orderGroupsExpanded: false, deliverySource: 'all', deliveryView: 'queue', deliveryStage: 'all', deliverySearch: '', auth: null, bridgeConfig: null, modalReturnFocus: null, appearance: null };
let publicOrderGeneration = 0;
const XIAN_YU_URL = '';
function configureCustomerBridge(catalog) {
  const bridge = window.MediaDockCustomerBridge;
  if (bridge?.configure) bridge.configure(catalog?.customer_center_url || '');
}
const esc = value => String(value ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
const money = value => (Number(value || 0) / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
const array = value => Array.isArray(value) ? value : (value && typeof value === 'object' ? Object.values(value) : []);
const oid = order => String(order.order_id ?? order.id ?? '');
const own = product => Boolean(product.requires_own_115);
const paths = {
  arrow:'M5 12h14m-5-5 5 5-5 5', check:'m5 12 4 4L19 6', external:'M14 3h7v7m0-7L10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5',
  key:'M15 7a5 5 0 1 1-3.8 8.25L3 23v-4l3-3h3l1.8-1.8A5 5 0 0 1 15 7Zm1 4h.01', box:'m3 7 9-4 9 4-9 4-9-4Zm0 0v10l9 4 9-4V7M12 11v10M7.5 5 17 9',
  close:'m6 6 12 12M6 18 18 6', info:'M12 8h.01M12 11v6M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0Z',
  grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z', order:'M8 3H5v18h14V3h-3M9 2h6v4H9zM8 10h8M8 14h8M8 18h5',
  chat:'M21 11a9 9 0 0 1-9 9H3l2-5a9 9 0 1 1 16-4ZM8 10h8M8 14h5', settings:'M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8ZM12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2',
  logout:'M9 4H4v16h5m-1-8h13m-4-4 4 4-4 4', refresh:'M20 7v5h-5m-11 5v-5h5M6 6a8 8 0 0 1 13 3M5 15a8 8 0 0 0 13 3',
  plus:'M12 5v14M5 12h14', search:'M21 21l-6-6M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z', copy:'M9 9h12v12H9zM15 5V3H3v12h2',
  shield:'M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6l-9-4Zm-5 10 3 3 7-7', clock:'M12 6v6l4 2M22 12A10 10 0 1 1 2 12a10 10 0 0 1 20 0Z',
  link:'M10 13a5 5 0 0 0 7 0l4-4a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-4 4a5 5 0 0 0 7 7l2-2', film:'M3 3h18v18H3zM7 3v18M17 3v18M3 8h4m-4 8h4M17 8h4m-4 8h4',
};
const icon = (name, size = 18) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.info}"/></svg>`;
const appearanceDefaults = {site_name:'影视服',site_subtitle:'MediaDock',site_description:'观影服务平台',logo_url:'',favicon_url:'',hero_eyebrow:'A LITTLE LIGHT. A WORLD OF STORIES.',hero_title:'把好电影，',hero_highlight:'留给每个夜晚。',hero_description:'让喧嚣留在门外，让故事照亮生活。\n从一场电影开始，拥有属于你的光影时刻。',footer_text:'让好影像融入生活',hero_images:[],theme_mode:'auto',day_start:'07:00',night_start:'19:00'};
const appearance = () => ({...appearanceDefaults,...state.appearance});
function decorationImage(value) {
  if(typeof value!=='string' || [...value].some(c=>c.trim()==='' || c.charCodeAt(0)===92))return '';
  if(/^\/(?:static\/|api\/public\/appearance-assets\/)/.test(value) && !value.includes('..'))return value;
  try {const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password?url.href:'';}catch{return '';}
}
function brand() {
  const a=appearance(),logo=decorationImage(a.logo_url);
  return `<span class="brand">${logo?`<img class="brand-logo" src="${esc(logo)}" alt="" width="40" height="40" referrerpolicy="no-referrer">`:'<span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>'}<span class="brand-name">${esc(a.site_name)}<small>${esc(a.site_subtitle)}</small></span></span>`;
}
function applyAppearance(value) {
  if(!value || typeof value!=='object' || Array.isArray(value))return;
  state.appearance={...appearanceDefaults,...value};
  const a=appearance(),favicon=document.querySelector('link[rel="icon"]');
  if(favicon){favicon.href=decorationImage(a.favicon_url)||'/static/favicon.svg';favicon.removeAttribute('type');}
  document.querySelector('meta[name="description"]')?.setAttribute('content',a.site_description);
  window.MediaDockTheme?.configure(a);
  document.querySelectorAll('.brand').forEach(element=>{element.outerHTML=brand();});
  updateSiteTitle();
}
function updateSiteTitle() {
  const a=appearance(),path=location.pathname;
  const labels={'/plans':'观影套餐','/library':'影视库','/discover':'订阅找片','/guide':'使用教程','/status':'服务状态','/claim':'领取卡密'};
  const label=path.startsWith('/admin')?'管理控制台':labels[path];
  document.title=label?label+' · '+a.site_name:a.site_name+' · '+a.site_description;
}
async function loadAppearance(){try{applyAppearance(await api('/api/public/appearance',{timeout:5000}));}catch{window.MediaDockTheme?.configure(appearance());}}
function safeUrl(value, { local = false } = {}) {
  if (!value) return '';
  try { const url = new URL(value, location.origin); return ['https:', 'http:'].includes(url.protocol) && (!local || url.origin === location.origin || url.protocol === 'https:') ? url.href : ''; } catch { return ''; }
}
function errorText(error) { return error?.message || '暂时无法完成，请稍后再试。'; }
async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeout || 90000);
  try {
    const response = await fetch(path, { credentials: 'same-origin', method: options.method || 'GET', headers: { 'Accept': 'application/json', ...(options.body !== undefined ? {'Content-Type':'application/json'} : {}), ...options.headers }, ...(options.body !== undefined ? {body: JSON.stringify(options.body)} : {}), signal: controller.signal });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.error) {
      const failure = new Error(data.error?.message || data.detail?.message || (typeof data.detail === 'string' ? data.detail : `请求未完成（${response.status}）`));
      failure.status = response.status; failure.code = data.error?.code; throw failure;
    }
    if (path === '/api/public/catalog') {configureCustomerBridge(data);applyAppearance(data.appearance);}
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求等待超时，请刷新订单状态后再操作；不要重复生成卡密。');
    if (error instanceof TypeError) throw new Error('暂时无法连接服务，请检查网络后重新读取状态。');
    throw error;
  } finally { clearTimeout(timer); }
}
function toast(message, error = false) {
  const root = document.getElementById('toast-root'); root.innerHTML = `<div class="toast${error ? ' error' : ''}">${esc(message)}</div>`;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { root.innerHTML = ''; }, 4500);
}
async function copy(text, label = '已复制') {
  try { await navigator.clipboard.writeText(text); toast(label); } catch { toast('浏览器未允许复制，请选中文本手动复制。', true); }
}
function busy(button, label = '处理中…') {
  const old = button.innerHTML; button.disabled = true; button.textContent = label;
  return () => { if (button.isConnected) { button.innerHTML = old; button.disabled = false; } };
}
function showModal(html, extraClass = '') {
  clearTimeout(announcementTimer);
  state.modalReturnFocus = document.activeElement;
  modalRoot.innerHTML = `<div class="overlay" role="presentation"><section class="modal ${extraClass}" role="dialog" aria-modal="true" aria-labelledby="modal-title" tabindex="-1"><button class="modal-close" data-action="close-modal" aria-label="关闭">${icon('close',20)}</button>${html}</section></div>`;
  document.body.classList.add('modal-open'); modalRoot.querySelector('.modal').focus();
}
function closeModal() { if (modalRoot.querySelector('.intro-modal[data-waiting="true"]')) return; clearTimeout(announcementTimer); modalRoot.innerHTML = ''; document.body.classList.remove('modal-open'); state.modalReturnFocus?.focus(); }
document.addEventListener('keydown', event => {
  if (!modalRoot.firstChild) return;
  if (event.key === 'Escape') closeModal();
  if (event.key === 'Tab') {
    const elements = [...modalRoot.querySelectorAll('a[href],button:not(:disabled),input:not(:disabled),select,textarea,[tabindex="0"]')].filter(el => el.offsetParent !== null);
    const first = elements[0], last = elements.at(-1);
    if (event.shiftKey && (document.activeElement === first || document.activeElement.classList.contains('modal'))) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
});
const productTitle = product => product.duration_days === 1 ? (own(product) ? '有网盘 · 试用天卡' : '无网盘 · 试用天卡') : product.duration_days === 30 ? '有网盘 · 月卡' : product.duration_days === 365 ? (own(product) ? '有网盘 · 年卡' : '无网盘 · 年卡') : product.name;
const productDescription = product => own(product) ? '自备 115 网盘，连接你的影视时光' : '无需自备网盘，使用负载均衡模式';
const durationLabel = product => `${product.duration_days} 天`;
function userCenterLink(label = '用户中心', className = '') {
  const url = safeUrl(state.catalog?.redemption_url || state.catalog?.customer_center_url);
  return url ? `<a class="${esc(className)}" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ${icon('external',13)}</a>` : `<button type="button" class="user-center-pending" data-action="user-center-unconfigured">${esc(label)}</button>`;
}
function xianyuLink(label = '闲鱼店铺') {
  const url = safeUrl(state.catalog?.xianyu_store_url);
  return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ${icon('external',13)}</a>` : '';
}
function header(active = '') { return `<header class="site-header"><a href="/" aria-label="${esc(appearance().site_name)}首页">${brand()}</a><nav class="site-nav" aria-label="主导航"><a class="${active === 'shop' ? 'active' : ''}" href="/">首页</a><a class="${active === 'plans' ? 'active' : ''}" href="/plans">观影套餐</a><a class="${active === 'library' ? 'active' : ''}" href="/library">影视库</a><a class="${active === 'discovery' ? 'active' : ''}" href="/discover">订阅找片</a><a class="${active === 'status' ? 'active' : ''}" href="/status">服务状态</a><a class="${active === 'guide' ? 'active' : ''}" href="/guide">使用教程</a><a class="${active === 'claim' ? 'active' : ''}" href="/claim">领取卡密</a>${userCenterLink('用户中心','user-center-link')}${window.MediaDockTheme?.control()||''}</nav></header>`; }
function footer() { return `<footer class="site-footer"><span>© ${new Date().getFullYear()} ${esc(appearance().site_subtitle)} ${esc(appearance().site_name)} · ${esc(appearance().footer_text)}</span><div class="footer-links"><button type="button" class="footer-intro-link" data-action="show-introduction">${esc(state.catalog?.introduction?.entry_label || appearance().site_name+'简介')}</button><a href="/plans">观影套餐</a><a href="/library">影视库</a><a href="/discover">订阅找片</a><a href="/status">服务状态</a><a href="/guide">使用教程</a><a href="/claim">领取卡密</a>${userCenterLink()}${xianyuLink()}${['127.0.0.1','localhost'].includes(location.hostname) ? `<a href="/admin">管理入口 ${icon('arrow',12)}</a>` : ''}</div></footer>`; }
const DEFAULT_INTRODUCTION = {"entry_label": "影视服简介", "enabled": true, "title": "影视服 · 观影服务平台", "subtitle": "统一浏览片库、选择套餐、领取卡密与查看服务状态。", "sections": [{"title": "统一观影入口", "body": "通过一个网站浏览已开放片库、搜索影片并提交求片申请。实际片库、线路与可用功能以部署方配置为准。"}, {"title": "套餐与卡密交付", "body": "选择适合自己的套餐，按页面指引完成购买与领取。请妥善保管领取链接，持有链接的人可能查看对应卡密。"}, {"title": "购买必看！先查片库，再决定购买", "body": "只是查看片库有没有想看的电影或剧集，直接搜索即可，无需购买试用卡或天卡。试用卡适合测试实际播放速度、流畅度和客户端体验。"}]};
const INTRO_SEEN_KEY = 'mediadock-introduction-seen';
let announcementSeen = false;
let announcementTimer = null;
function introWasSeen() {
  if (announcementSeen) return true;
  for (const storageName of ['localStorage', 'sessionStorage']) {
    try { if (window[storageName].getItem(INTRO_SEEN_KEY) === '1') return true; } catch { /* Storage can be unavailable in private contexts. */ }
  }
  return false;
}
function rememberIntro() {
  announcementSeen = true;
  for (const storageName of ['localStorage', 'sessionStorage']) {
    try { window[storageName].setItem(INTRO_SEEN_KEY, '1'); } catch { /* The in-page fallback still prevents repeat prompts. */ }
  }
}
function showIntroduction(automatic = false, intro = state.catalog?.introduction || DEFAULT_INTRODUCTION, preview = false) {
  if (automatic && !intro.enabled) return;
  if (automatic && (location.pathname !== '/' || new URLSearchParams(location.search).has('order') || new URLSearchParams(location.search).has('review') || introWasSeen() || modalRoot.firstChild)) return;
  showModal(`<header class="intro-heading"><div class="eyebrow">MediaDock / 影视服</div><h2 id="modal-title">${esc(intro.title)}</h2><p>${esc(intro.subtitle)}</p></header>
    <div class="intro-scroll" tabindex="0" role="region" aria-label="${esc(intro.entry_label || '影视服简介')}正文">${intro.sections.map(section=>`<section class="${section.title.includes('购买必看')?'intro-purchase':''}"><h3>${esc(section.title)}</h3><p>${esc(section.body)}</p></section>`).join('')}</div>
    <div class="intro-actions"><p>${preview?'这是未保存内容的预览。':'随时可在页脚「'+esc(intro.entry_label || '影视服简介')+'」重新查看。'}</p><button type="button" class="btn btn-primary btn-full" data-action="close-modal" data-intro-ack ${automatic?'disabled':''}>${automatic?'了解（1 秒后可点击）':'了解'}</button></div>`, 'intro-modal');
  const dialog = modalRoot.querySelector('.intro-modal');
  dialog.dataset.waiting = String(automatic);
  const close = dialog.querySelector('.modal-close'), ack = dialog.querySelector('[data-intro-ack]');
  close.disabled = automatic;
  if (!preview) rememberIntro();
  if (automatic) announcementTimer = setTimeout(() => {
    if (!dialog.isConnected) return;
    dialog.dataset.waiting = 'false'; close.disabled = false; ack.disabled = false; ack.textContent = '了解';
    announcementTimer = null;
  }, 1000);
}
function empty(message, title = '这里暂时还没有内容', button = '') { return `<div class="empty">${icon('box',29)}<h3>${esc(title)}</h3><p>${esc(message)}</p>${button}</div>`; }
function sortedProducts(products) { return array(products).sort((a,b) => a.duration_days - b.duration_days || a.listed_price_cents - b.listed_price_cents); }
function productCards() {
  const products = sortedProducts(state.catalog.products).filter(p => state.filter === 'all' || (state.filter === 'own' ? own(p) : !own(p)));
  return products.map(product => `<article class="product ${product.duration_days === 1 ? 'product-trial' : ''}"><div class="product-top"><span class="product-tier">${product.duration_days === 1 ? 'ONE DAY / 试用' : product.duration_days === 30 ? 'MONTHLY / 月度' : 'ANNUAL / 年度'}</span><span class="pill ${own(product) ? 'own' : 'managed'}">${own(product) ? '自备 115 网盘' : '无需自备网盘'}</span></div><h3>${esc(productTitle(product))}</h3><p class="product-description">${esc(productDescription(product))}</p><div class="price"><span class="currency">¥</span>${money(product.listed_price_cents)}<small>/ ${esc(durationLabel(product))}</small></div><ul class="features"><li>${icon('check',14)}${esc(durationLabel(product))}使用时长</li><li>${icon('check',14)}${own(product) ? '使用自己的 115 网盘' : '负载均衡模式'}</li><li>${icon('check',14)}影视服卡密兑换</li><li>${icon('check',14)}每单一张专属卡密</li></ul><button class="btn ${product.duration_days === 1 ? 'btn-secondary' : 'btn-primary'} btn-full" data-action="purchase" data-product="${esc(product.id)}">选择此套餐 ${icon('arrow',15)}</button></article>`).join('') || empty('当前筛选条件下没有可选套餐。');
}
const shopNetworkGroups = [
  {id:'cdn',name:'CDN 监测',nodes:[{id:'cdn',name:'CDN 线路'}]},
  {id:'origin',name:'源站监测',nodes:[{id:'origin',name:'源站'}]},
  {id:'relays',name:'线路机监测',nodes:[{id:'relay-1',name:'线路 1'}]},
];
const shopNetworkStatuses = {unconfigured:['待接入','neutral'],checking:['监测中','neutral'],client_pending:['等待本机测速','neutral'],healthy:['正常','healthy'],degraded:['响应较慢','degraded'],down:['暂不可用','down'],unknown:['尚无数据','neutral']};
const shopNetworkMonitor = {active:false,timer:null,expiryTimer:null,controller:null,generation:0,data:null,failed:false,browserRound:0,browserRunning:false,browserSignature:'',browserControllers:new Set(),browserResults:new Map()};
function networkCheckedAt(value) {
  if(typeof value!=='string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)$/.test(value)) return null;
  const timestamp=Date.parse(value);
  return Number.isFinite(timestamp) && timestamp<=Date.now()+60000 ? timestamp : null;
}
function validNetworkId(value) { return typeof value==='string' && /^[a-z][a-z0-9-]{0,63}$/.test(value); }
function validBrowserMonitorUrl(value) {
  if(typeof value!=='string' || !/^https:\/\/[^/]/i.test(value) || value.length>2048 || /[\s\p{C}\\?#]/u.test(value)) return false;
  try {const url=new URL(value);return url.protocol==='https:' && Boolean(url.hostname) && !url.username && !url.password && !url.search && !url.hash;}
  catch {return false;}
}
function networkStatusData(data) {
  if(!data || !Array.isArray(data.groups)) throw new Error('Unavailable status');
  // Copy only validated public fields; never retain probe labels, targets or diagnostics.
  const groupIds=new Set(),nodeIds=new Set();let nodeCount=0;
  const groups=data.groups.slice(0,12).filter(group=>{
    if(!group || !validNetworkId(group.id) || groupIds.has(group.id) || !Array.isArray(group.nodes)) return false;
    groupIds.add(group.id);return true;
  }).map(group=>({id:group.id,name:validNetworkDisplayName(group.name)?group.name.trim():'监测分组',nodes:group.nodes.filter(node=>{
    if(!node || !validNetworkId(node.id) || nodeIds.has(node.id) || nodeCount>=40) return false;
    nodeIds.add(node.id);nodeCount++;return true;
  }).map(node=>{
    let status=Object.hasOwn(shopNetworkStatuses,node.status)?node.status:'unknown';
    const checkedAt=networkCheckedAt(node.checked_at);
    if(['healthy','degraded','down'].includes(status) && (!checkedAt || Date.now()-checkedAt>=180000)) status='unknown';
    const latency=node.latency_ms,basis=['http','icmp','mmwx','browser'].includes(node.basis)?node.basis:'http';
    const result={id:node.id,name:validNetworkDisplayName(node.name)?node.name.trim():'监测节点',basis,status,checkedAt,latencyMs:basis!=='browser' && ['healthy','degraded'].includes(status) && typeof latency==='number' && Number.isFinite(latency) && latency>=0 && latency<=60000?Math.round(latency):null};
    // Only explicitly public browser test addresses may enter browser state.
    if(basis==='browser') {result.testUrl=validBrowserMonitorUrl(node.test_url)?node.test_url:null;result.checkedAt=null;result.status=result.testUrl?'client_pending':status==='unconfigured'?'unconfigured':'unknown';}
    return result;
  })}));
  return {checkedAt:networkCheckedAt(data.checked_at),groups};
}
function networkTime(timestamp) {
  return timestamp===null ? '' : new Date(timestamp).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
}
function shopNetworkStatusMarkup() {
  const data=shopNetworkMonitor.data;
  const groups=data?.groups || [];
  const now=Date.now();
  const cards=groups.map(group=>`<article class="network-monitor-card${group.nodes.length>1?' network-monitor-wide':''}"><div class="network-monitor-card-heading">${icon(group.id==='cdn'?'grid':group.id==='origin'?'box':'link',16)}<h4>${esc(group.name)}</h4></div><ul>${group.nodes.map(node=>{
    if(node.basis==='browser') {
      const result=shopNetworkMonitor.browserResults.get(node.id),fresh=result?.checkedAt && now-result.checkedAt<180000;
      const measured=fresh && typeof result.latencyMs==='number';
      const label=node.status==='unconfigured'?'待接入':measured?(result.httpError?'服务响应异常':result.sampleCount<3?'响应不稳定':result.opaque?'连接有响应':'请求成功'):result?.state==='testing'?'正在测试':'暂不可测';
      const detail=measured?`HTTP 响应 ${result.latencyMs} ms · 非 Ping`:node.status==='unconfigured'?'浏览器测速尚未接入':result?.state==='testing'?'正在预热并测量当前网络':result?.state==='insufficient'?'有效响应不足，请稍后重试':'当前网络未获得有效响应';
      const tone=measured?(result.httpError || result.sampleCount<3?'degraded':result.opaque?'neutral':'healthy'):'neutral';
      return `<li><div class="network-node-copy"><strong>${esc(node.name)}</strong><small>${detail}</small></div><span class="network-node-status ${tone}"><i aria-hidden="true"></i>${label}</span></li>`;
    }
    let status=node.status || (shopNetworkMonitor.failed?'unknown':'checking');
    if(['healthy','degraded','down'].includes(status) && (!node.checkedAt || now-node.checkedAt>=180000)) status='unknown';
    const [baseLabel,tone]=shopNetworkStatuses[status];
    const label=node.basis==='mmwx'?({healthy:'心跳正常',degraded:'心跳正常',down:'心跳离线',unknown:'心跳未知'}[status]||baseLabel):baseLabel;
    const hasLatency=['healthy','degraded'].includes(status) && node.latencyMs!==undefined && node.latencyMs!==null;
    const detail=node.basis==='mmwx' && !['unconfigured','checking'].includes(status)?hasLatency?`响应 ${node.latencyMs} ms`:'响应暂无数据':hasLatency?`${node.basis==='icmp'?'服务端 Ping':'服务端响应'} ${node.latencyMs} ms`:status==='unconfigured'?'监测尚未接入':status==='unknown'?'等待有效监测数据':status==='checking'?'正在读取监测状态':status==='down'?'连接未通过监测':'';
    return `<li><div class="network-node-copy"><strong>${esc(node.name)}</strong><small>${detail}</small></div><span class="network-node-status ${tone}"><i aria-hidden="true"></i>${label}</span></li>`;
  }).join('') || '<li class="network-monitor-empty">暂无监测项目</li>'}</ul></article>`).join('');
  const hasServer=groups.some(group=>group.nodes.some(node=>node.basis!=='browser')),hasBrowser=groups.some(group=>group.nodes.some(node=>node.basis==='browser'));
  const time=hasServer && data?.checkedAt ? networkTime(data.checkedAt) : '';
  const updated=shopNetworkMonitor.failed?'监测状态暂不可用，稍后自动重试':time?`探针 / 服务端更新 ${time}`:hasServer?'探针 / 服务端尚无记录':'';
  const browserTime=Math.max(0,...[...shopNetworkMonitor.browserResults.values()].map(result=>result.checkedAt||0));
  return `${cards?`<div class="network-monitor-grid">${cards}</div>`:`<p class="network-monitor-empty">${data?'尚未配置监测':shopNetworkMonitor.failed?'监测状态暂不可用':'正在读取监测状态…'}</p>`}<div class="network-monitor-foot">${updated?`<span>${updated}</span>`:''}${hasBrowser?`<span>${browserTime?`你的网络测速更新 ${networkTime(browserTime)}`:'你的网络测速等待响应'}</span>`:''}<span>每分钟刷新状态${hasBrowser?' · 浏览器测速按队列进行':''}</span></div>`;
}
function renderShopNetworkStatus() {
  clearTimeout(shopNetworkMonitor.expiryTimer);
  shopNetworkMonitor.expiryTimer=null;
  const root=document.getElementById('network-status');
  if(!root) return;
  const now=Date.now();
  root.innerHTML=shopNetworkStatusMarkup();
  if(!shopNetworkMonitor.active || document.visibilityState==='hidden') return;
  // Expire cached samples locally at their own deadline; no additional probe/request.
  const deadlines=(shopNetworkMonitor.data?.groups || []).flatMap(group=>group.nodes)
    .filter(node=>node.basis!=='browser' && ['healthy','degraded','down'].includes(node.status) && node.checkedAt!==null)
    .map(node=>node.checkedAt+180000).concat([...shopNetworkMonitor.browserResults.values()].filter(result=>result.checkedAt).map(result=>result.checkedAt+180000)).filter(deadline=>deadline>now);
  if(deadlines.length) shopNetworkMonitor.expiryTimer=setTimeout(renderShopNetworkStatus,Math.max(1,Math.min(...deadlines)-now));
}
function stopShopNetworkMonitor() {
  shopNetworkMonitor.active=false;
  shopNetworkMonitor.generation++;
  clearTimeout(shopNetworkMonitor.timer);
  clearTimeout(shopNetworkMonitor.expiryTimer);
  shopNetworkMonitor.timer=null;
  shopNetworkMonitor.expiryTimer=null;
  shopNetworkMonitor.controller?.abort();
  shopNetworkMonitor.controller=null;
  cancelBrowserNetworkMeasurements();
  shopNetworkMonitor.browserResults.clear();
}
function cancelBrowserNetworkMeasurements() {
  shopNetworkMonitor.browserRound++;
  shopNetworkMonitor.browserRunning=false;shopNetworkMonitor.browserSignature='';
  for(const controller of shopNetworkMonitor.browserControllers) controller.abort();
  shopNetworkMonitor.browserControllers.clear();
}
function browserNetworkRoundCurrent(round,generation) {
  return shopNetworkMonitor.active && shopNetworkMonitor.browserRound===round && shopNetworkMonitor.generation===generation && document.visibilityState!=='hidden' && Boolean(document.getElementById('network-status'));
}
async function sampleBrowserNetwork(url,round,generation) {
  if(!browserNetworkRoundCurrent(round,generation)) return null;
  const controller=new AbortController();shopNetworkMonitor.browserControllers.add(controller);
  const timeout=setTimeout(()=>controller.abort(),5000),started=performance.now();
  try {
    // The Fetch standard requires follow for no-cors. Exact connect-src origins
    // still restrict redirects; configured test endpoints should respond directly.
    // no-store prevents local HTTP cache hits from masquerading as network time.
    // It may trigger CDN revalidation, so this is HTTP response time, not edge RTT.
    const response=await fetch(url,{method:'HEAD',mode:'no-cors',credentials:'omit',redirect:'follow',cache:'no-store',referrerPolicy:'no-referrer',signal:controller.signal});
    const elapsed=performance.now()-started;
    if(controller.signal.aborted || !browserNetworkRoundCurrent(round,generation) || !Number.isFinite(elapsed) || elapsed<0 || elapsed>60000) return null;
    const opaque=response.type==='opaque';
    if(!opaque && (!Number.isInteger(response.status) || response.status<100 || response.status>599)) return null;
    if(!opaque && [405,501].includes(response.status)) return null;
    // HEAD measures header arrival without downloading the target page body.
    // Still cancel/abort promptly to close any unexpected body or connection.
    // An opaque response proves a response arrived, not a successful HTTP status.
    response.body?.cancel()?.catch(()=>{});
    controller.abort();
    return {latencyMs:Math.round(elapsed),opaque,httpError:!opaque && (response.status<200 || response.status>=300)};
  } catch {return null;}
  finally {clearTimeout(timeout);controller.abort();shopNetworkMonitor.browserControllers.delete(controller);}
}
async function refreshBrowserNetworkMeasurements(generation=shopNetworkMonitor.generation) {
  const nodes=(shopNetworkMonitor.data?.groups||[]).flatMap(group=>group.nodes).filter(node=>node.basis==='browser' && node.testUrl);
  const signature=JSON.stringify(nodes.map(node=>[node.id,node.testUrl]));
  // A slow 40-node queue may cross the status refresh interval. Do not starve
  // later nodes by restarting an unchanged queue every minute.
  if(shopNetworkMonitor.browserRunning && shopNetworkMonitor.browserSignature===signature) return;
  cancelBrowserNetworkMeasurements();
  const round=shopNetworkMonitor.browserRound;
  shopNetworkMonitor.browserSignature=signature;
  shopNetworkMonitor.browserResults=new Map(nodes.map(node=>[node.id,{state:'testing',checkedAt:null,latencyMs:null}]));
  if(!browserNetworkRoundCurrent(round,generation)) return;
  shopNetworkMonitor.browserRunning=true;
  renderShopNetworkStatus();
  let next=0;
  async function worker() {
    while(browserNetworkRoundCurrent(round,generation) && next<nodes.length) {
      const node=nodes[next++],samples=[];
      // Warm the connection without letting its cold-start time enter the median.
      // A failed warm-up does not suppress the three actual measurements.
      await sampleBrowserNetwork(node.testUrl,round,generation);
      for(let attempt=0;attempt<3 && browserNetworkRoundCurrent(round,generation);attempt++) {
        const sample=await sampleBrowserNetwork(node.testUrl,round,generation);if(sample) samples.push(sample);
      }
      if(!browserNetworkRoundCurrent(round,generation)) return;
      const values=samples.map(sample=>sample.latencyMs).sort((a,b)=>a-b),middle=Math.floor(values.length/2);
      const latencyMs=values.length>=2?Math.round(values.length%2?values[middle]:(values[middle-1]+values[middle])/2):null;
      shopNetworkMonitor.browserResults.set(node.id,{state:values.length>=2?'responded':values.length?'insufficient':'unavailable',latencyMs,sampleCount:values.length,checkedAt:Date.now(),opaque:samples.some(sample=>sample.opaque),httpError:samples.some(sample=>sample.httpError)});
      renderShopNetworkStatus();
    }
  }
  await Promise.all(Array.from({length:Math.min(3,nodes.length)},worker));
  if(browserNetworkRoundCurrent(round,generation)) shopNetworkMonitor.browserRunning=false;
}
async function refreshShopNetworkStatus() {
  if(!shopNetworkMonitor.active || document.visibilityState==='hidden' || !document.getElementById('network-status')) return;
  const generation=shopNetworkMonitor.generation;
  const controller=new AbortController();
  shopNetworkMonitor.controller=controller;
  const timeout=setTimeout(()=>controller.abort(),10000);
  try {
    const response=await fetch('/api/public/network-status',{method:'GET',credentials:'omit',cache:'no-store',headers:{Accept:'application/json'},signal:controller.signal});
    if(!response.ok) throw new Error('Unavailable status');
    const data=networkStatusData(await response.json());
    if(generation!==shopNetworkMonitor.generation) return;
    shopNetworkMonitor.data=data;
    shopNetworkMonitor.failed=false;
    void refreshBrowserNetworkMeasurements(generation);
  } catch {
    if(generation!==shopNetworkMonitor.generation) return;
    shopNetworkMonitor.data=null;
    shopNetworkMonitor.failed=true;
    cancelBrowserNetworkMeasurements();shopNetworkMonitor.browserResults.clear();
  } finally {
    clearTimeout(timeout);
    if(generation===shopNetworkMonitor.generation) {
      shopNetworkMonitor.controller=null;
      renderShopNetworkStatus();
      if(shopNetworkMonitor.active && document.visibilityState!=='hidden' && document.getElementById('network-status')) shopNetworkMonitor.timer=setTimeout(refreshShopNetworkStatus,60000);
    }
  }
}
function startShopNetworkMonitor() {
  stopShopNetworkMonitor();
  if(document.visibilityState==='hidden' || !document.getElementById('network-status')) return;
  shopNetworkMonitor.active=true;
  shopNetworkMonitor.data=null;
  shopNetworkMonitor.failed=false;
  renderShopNetworkStatus();
  void refreshShopNetworkStatus();
}
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='hidden') stopShopNetworkMonitor();
  else if(document.getElementById('network-status')) startShopNetworkMonitor();
});
window.addEventListener('pagehide',stopShopNetworkMonitor);
window.addEventListener('pageshow',()=>{if(!shopNetworkMonitor.active && document.getElementById('network-status')) startShopNetworkMonitor();});
function shopNetworkSection() {
  return `<section class="status-content" id="network" aria-labelledby="network-title">
    <div class="status-heading"><div class="eyebrow">MediaDock / SERVICE STATUS</div><h1 id="network-title">服务状态</h1><p>线路表现，一目了然。查看你与 CDN 的连接，以及源站、线路机的运行状态。</p></div>
    <div class="status-overview"><article class="status-provider"><small>CDN NETWORK · 服务线路</small><h2>CDN 线路状态</h2><p>查看部署方配置的线路状态。实际连接体验受网络、服务端及客户端等因素影响。</p><footer><span>监测节点</span><span>多线路协同</span></footer></article><article class="status-guide"><h2>读懂每一种响应</h2><dl><dt>CDN · 你的网络</dt><dd>测量当前浏览器等待测速地址返回响应的时间，包含网络传输与服务处理；不是节点 Ping 延迟，也不是下载速度。</dd><dt>源站 / 线路机 · 探针数据</dt><dd>展示探针上报的平均响应与心跳状态，数值随监测自动更新。</dd></dl></article></div>
    <section class="status-panel" aria-labelledby="network-policy-title"><div class="status-panel-head"><div><h2 id="network-policy-title">${icon('shield',20)} CDN 负载权重 × 健康监测</h2><p>通过负载权重协调多线路流量，结合健康监测，为线路调度提供依据。</p></div><button type="button" class="btn btn-outline btn-small" data-action="network-refresh">${icon('refresh',14)}重新检测</button></div><div id="network-status" aria-live="polite" aria-atomic="true">${shopNetworkStatusMarkup()}</div><p class="network-monitor-note">HTTP 响应包含网络及测速服务处理耗时，不等同于 Ping；连接有响应不代表 HTTP 业务成功。若测速地址需要回源，还会包含 CDN 到源站的耗时。探针响应为监测目标的平均值，并非访客延迟。各项监测均不代表完整播放体验。</p></section>
  </section>`;
}
function homeCarouselMarkup(slides=[], featured=true) {
  if(!slides.length)return '<div class="hero-deck"><div class="carousel-loading" role="status">正在读取热门影片…</div></div>';
  return `<div class="hero-deck">${slides.map((slide,index)=>{
    const backdrop=featured && /^\/api\/public\/featured-images\/[a-f0-9]{64}\.(?:jpg|png|webp)$/.test(slide.backdrop_url||'') ? slide.backdrop_url : '';
    const artwork=backdrop?'backdrop':featured?'poster':'local';
    return `<article class="hero-slide" data-artwork="${artwork}" data-carousel-slide data-position="${index===0?'active':index===1?'next':index===slides.length-1?'previous':'hidden'}" aria-roledescription="幻灯片" aria-label="${index+1} / ${slides.length}" aria-hidden="${index!==0}" ${index?'inert':''}><img class="hero-slide-image" src="${esc(backdrop||slide.poster_url)}" alt="${esc(featured?slide.title+(backdrop?' 剧照':' 海报'):slide.title+' · '+appearance().site_name)}" width="${artwork==='poster'?600:1280}" height="${artwork==='poster'?900:720}" decoding="async" ${index?'loading="lazy"':'fetchpriority="high"'} referrerpolicy="no-referrer"><div class="hero-slide-caption"><span class="hero-slide-kicker">${featured?`本周热门 · ${String(index+1).padStart(2,'0')}`:esc(slide.kicker)}</span><h2>${esc(slide.title)}</h2><p>${featured?'发现一部好电影':esc(slide.description)}</p></div></article>`;}).join('')}</div><div class="hero-carousel-controls"><button type="button" class="carousel-arrow" data-carousel-prev aria-label="上一张">←</button><div class="carousel-dots" role="group" aria-label="选择轮播内容">${slides.map((_,index)=>`<button type="button" data-carousel-dot="${index}" class="${index===0?'is-active':''}" aria-label="切换到第 ${index+1} 张" aria-pressed="${index===0}"></button>`).join('')}</div><span class="carousel-count">01 / ${String(slides.length).padStart(2,'0')}</span><button type="button" class="carousel-arrow" data-carousel-next aria-label="下一张">→</button><button type="button" class="carousel-pause" data-carousel-pause aria-label="暂停轮播">暂停轮播</button></div>`;
}
function homeHero() {
  return `<section class="home-hero" aria-labelledby="home-title"><div class="home-hero-copy"><div class="eyebrow">${esc(appearance().hero_eyebrow)}</div><h1 id="home-title">${esc(appearance().hero_title)}<br><span>${esc(appearance().hero_highlight)}</span></h1><p class="hero-copy hero-custom-description">${esc(appearance().hero_description)}</p><div class="home-actions"><a class="btn btn-primary" href="/plans">探索观影套餐 ${icon('arrow',17)}</a><a class="btn btn-outline" href="/library">逛逛影视库 ${icon('film',16)}</a></div><div class="home-details"><span>原盘 · RE / REMUX</span><span>多线路支持</span><span>自备网盘 / 免网盘</span></div></div><div class="home-hero-visual"><div id="home-carousel" class="hero-carousel" role="region" aria-roledescription="轮播" aria-label="${esc(appearance().site_name)}光影轮播" tabindex="0">${homeCarouselMarkup()}</div></div></section>`;
}
function homeServiceLinks() {
  return `<section class="home-service-links" aria-label="快捷入口"><a class="home-service-link" href="/library"><small>01 / LIBRARY</small><h2>今晚，从哪部开始？</h2><p>浏览影视库，发现感兴趣的电影与剧集。</p><span class="action-label">逛逛影视库 ${icon('arrow',16)}</span></a><a class="home-service-link" href="/discover"><small>02 / DISCOVER</small><h2>好故事，值得继续期待。</h2><p>搜索想看的影片，提交申请或连接用户中心订阅。</p><span class="action-label">订阅与找片 ${icon('arrow',16)}</span></a><a class="home-service-link" href="/status"><small>03 / STATUS</small><h2>每一条线路，都有状态。</h2><p>查看 CDN 响应、源站与线路机监测。</p><span class="action-label">查看服务状态 ${icon('arrow',16)}</span></a></section>`;
}
function plansContent() {
  return `<div class="section-heading"><div><div class="eyebrow">FIND YOUR PLAN</div><h1>为你的观影方式，选一张卡</h1><p>有 115 网盘与无网盘两种模式，按需选择。</p></div><div class="segmented" role="group" aria-label="按网盘类型筛选"><button data-action="filter" data-filter="all" class="active" aria-pressed="true">全部套餐</button><button data-action="filter" data-filter="own" aria-pressed="false">我有 115 网盘</button><button data-action="filter" data-filter="managed" aria-pressed="false">我没有网盘</button></div></div><details class="purchase-notice" id="plans-purchase-notice"><summary><span class="purchase-notice-tag">购买必看！</span><span class="purchase-notice-summary"><strong>查片直接搜索，无需购买试用卡或天卡</strong><span>试用卡用于测试实际播放体验 · 点击展开说明</span></span></summary><div class="purchase-notice-body"><p><strong>购买前，先查看已开放片库有没有你想看的影片。</strong>如果只是确认收录情况，直接搜索即可。</p><p><strong>试用卡推荐用于测试实际播放速度、流畅度和客户端使用体验。</strong>需要亲自试播时，再按需选择。</p><div class="purchase-notice-actions"><a class="btn btn-primary" href="/library">先查片库</a><a class="btn btn-outline" href="/guide#guide-library">查看完整购买说明</a></div></div></details><div id="products" class="products">${productCards()}</div><div class="callout shop-note">${icon('info',17)}<div>${state.catalog.payment_configured?'购卡后请按订单页说明支付，到账核对完成后即可领取卡密。':'网页收款尚未配置，请通过其它入口完成购买。'}<br>自备网盘套餐需要你拥有自己的 115 网盘，卡密仅用于兑换对应服务时长。</div></div><div class="callout shop-note claim-warning">${icon('shield',17)}<div><strong>请妥善保管领取链接</strong><br>领取链接本身就是卡密凭证，持有链接的人可以查看对应卡密。链接目前可重复使用，暂不自动过期，请勿发布到公开群、评论区或可转发页面，只发送给实际购买者。</div></div>`;
}
function homeHowItWorks() {
  return `<section class="how-it-works"><div class="how-title"><h2>三步，开启你的影院</h2><p>A SIMPLE WAY TO GET STARTED</p></div><div class="step"><span class="step-num">01</span><div><h3>选择合适套餐</h3><p>根据是否自备 115 网盘，<br>选择体验、月卡或年卡。</p></div></div><div class="step"><span class="step-num">02</span><div><h3>付款后领取卡密</h3><p>等待到账确认，<br>在订单页获取卡密。</p></div></div><div class="step"><span class="step-num">03</span><div><h3>兑换，开始观影</h3><p>前往${userCenterLink('用户中心')||'用户中心'}，<br>使用卡密兑换服务时长。</p></div></div></section>`;
}
function updatePublicNavigation(active) {
  const page=document.querySelector(active==='status'?'.status-page':active==='library'?'.library-page':active==='discovery'?'.discover-page':active==='plans'?'.plans-page':'.storefront-page');
  if(!page) return;
  page.querySelector('.site-header').outerHTML=header(active);
  page.querySelector('.site-footer').outerHTML=footer();
}
async function loadHomeFeatured(root) {
  const custom=array(appearance().hero_images).filter(item=>decorationImage(item.url)).slice(0,6);
  if(custom.length){
    root.innerHTML=homeCarouselMarkup(custom.map(item=>({poster_url:decorationImage(item.url),title:item.title,description:item.subtitle,kicker:appearance().site_name})),false);
    const attribution=document.getElementById('home-carousel-source');if(attribution)attribution.hidden=true;
    root.setAttribute('aria-busy','false');window.MediaDockCarousel?.start(root);return;
  }
  root.setAttribute('aria-busy','true');
  const fallback=document.getElementById('home-carousel-source');
  if(fallback){fallback.hidden=false;fallback.textContent='正在读取本周热门影片…';}
  try {
    const result=await api('/api/public/featured-movies',{timeout:8000});
    const movies=array(result.movies).slice(0,6).filter(movie=>typeof movie.title==='string' && movie.title.length<=200 && /^\/api\/public\/featured-images\/[a-f0-9]{64}\.(?:jpg|png|webp)$/.test(movie.poster_url) && /^https:\/\/www\.themoviedb\.org\/movie\/[0-9]+$/.test(movie.url));
    if(!root.isConnected || root!==document.getElementById('home-carousel') || location.pathname!=='/' || new URLSearchParams(location.search).get('order') || movies.length===0) return;
    root.innerHTML=homeCarouselMarkup(movies,true);
    root.setAttribute('aria-label','本周热门电影轮播');
    const timestamp=networkCheckedAt(result.updated_at),date=timestamp?new Date(timestamp).toLocaleDateString('zh-CN'):'';
    const attribution=document.getElementById('home-carousel-source');
    attribution.innerHTML=`<img class="tmdb-logo" src="/static/tmdb-logo.svg" alt="TMDB" width="40" height="29"><span>本周热门${date?` · ${result.stale?'上次更新':'更新于'} ${esc(date)}`:''} · 影片资讯，不代表片库已收录</span><small>This product uses the TMDB API but is not endorsed or certified by TMDB.</small>`;
    attribution.hidden=false;
    root.querySelectorAll('img').forEach(image=>image.addEventListener('error',()=>{image.hidden=true;image.removeAttribute('src');},{once:true}));
    if(window.MediaDockCarousel?.refresh) window.MediaDockCarousel.refresh(root);
    else window.MediaDockCarousel?.start(root);
  } catch { if(root.isConnected&&fallback)fallback.textContent='热门影片暂时无法读取，请稍后刷新'; }
  finally {
    root.setAttribute('aria-busy','false');
    if(root.isConnected&&!root.querySelector('[data-carousel-slide]')){const placeholder=root.querySelector('.carousel-loading');if(placeholder)placeholder.textContent='热门影片暂时未就绪';}
    if(root.isConnected&&fallback&&fallback.textContent==='正在读取本周热门影片…')fallback.textContent='热门影片正在准备中';
  }
}
async function renderMediaPage(kind) {
  window.MediaDockCarousel?.stop();stopShopNetworkMonitor();window.MediaDockMedia?.stop();
  document.title=(kind==='library'?'影视库':'订阅找片')+' · '+appearance().site_name;
  app.innerHTML=`<div class="container ${kind==='library'?'library-page':'discover-page'}">${header(kind)}${window.MediaDockMedia?.markup(kind)||'<main class="page-center"><p>页面暂时无法加载，请刷新重试。</p></main>'}${footer()}</div>`;
  const page=app.firstElementChild;
  window.MediaDockMedia?.mount(kind);
  try {const catalog=await api('/api/public/catalog',{timeout:10000});if(page.isConnected){state.catalog=catalog;updatePublicNavigation(kind);}}catch{}
}
async function renderGuide() {
  window.MediaDockCarousel?.stop();stopShopNetworkMonitor();
  document.title='使用教程 · '+appearance().site_name;
  app.innerHTML=`<div class="container guide-page">${header('guide')}${window.MediaDockGuide?.markup({userCenterUrl:safeUrl(state.catalog?.redemption_url),guide:state.catalog?.guide})||'<main class="page-center"><h1>使用教程</h1><p>教程暂时无法加载，请刷新页面。</p></main>'}${footer()}</div>`;
  const page=app.firstElementChild;
  try {const catalog=await api('/api/public/catalog',{timeout:10000});if(page.isConnected){state.catalog=catalog;page.querySelector('.site-header').outerHTML=header('guide');page.querySelector('.site-footer').outerHTML=footer();const main=page.querySelector('main');if(window.MediaDockGuide && main)main.outerHTML=window.MediaDockGuide.markup({userCenterUrl:safeUrl(catalog.redemption_url),guide:catalog.guide});}}catch{}
}
async function renderServiceStatus() {
  window.MediaDockCarousel?.stop();stopShopNetworkMonitor();
  document.title='服务状态 · '+appearance().site_name;
  app.innerHTML=`<div class="container status-page">${header('status')}<main>${shopNetworkSection()}</main>${footer()}</div>`;
  const page=app.firstElementChild;
  startShopNetworkMonitor();
  try {const catalog=await api('/api/public/catalog',{timeout:10000});if(page.isConnected){state.catalog=catalog;updatePublicNavigation('status');}}catch{/* Monitoring works independently of the catalog. */}
}
async function renderShop() {
  const generation=++publicOrderGeneration;
  window.MediaDockCarousel?.stop();stopShopNetworkMonitor();
  updateSiteTitle();
  const parameters=new URLSearchParams(location.search),orderId=parameters.get('order'),reviewId=parameters.get('review');
  if(orderId || reviewId){
    try{const catalog=await api('/api/public/catalog');if(generation!==publicOrderGeneration)return;state.catalog=catalog;}catch{}
    if(generation!==publicOrderGeneration)return;
    return reviewId ? renderPurchaseReview(reviewId) : renderPublicOrder(orderId);
  }
  app.innerHTML=`<div class="container storefront-page storefront-home">${header('shop')}<main>${homeHero()}${homeServiceLinks()}</main><div class="home-footer">${footer()}<aside class="home-attribution" id="home-carousel-source" aria-label="影片资料来源" hidden></aside></div></div>`;
  const page=app.firstElementChild,carousel=document.getElementById('home-carousel');
  window.MediaDockCarousel?.start(carousel);void loadHomeFeatured(carousel);
  try {
    const catalog=await api('/api/public/catalog',{timeout:15000});
    if(!page.isConnected)return;state.catalog=catalog;updatePublicNavigation('shop');showIntroduction(true);
  }catch{/* Home and its links remain usable while catalog settings are unavailable. */}
}
async function renderPlans() {
  window.MediaDockCarousel?.stop();stopShopNetworkMonitor();
  document.title='观影套餐 · '+appearance().site_name;state.filter='all';
  app.innerHTML=`<div class="container storefront-page plans-page">${header('plans')}<main><section id="plans" aria-label="观影套餐"><p class="micro" role="status">正在读取观影套餐…</p></section><div id="plans-how"></div></main>${footer()}</div>`;
  const page=app.firstElementChild;
  try {
    const catalog=await api('/api/public/catalog',{timeout:15000});
    if(!page.isConnected)return;state.catalog=catalog;updatePublicNavigation('plans');
    page.querySelector('#plans').innerHTML=plansContent();page.querySelector('#plans-how').innerHTML=homeHowItWorks();
  }catch(error){if(page.isConnected)page.querySelector('#plans').innerHTML=empty(errorText(error),'套餐暂时无法加载','<button class="btn btn-secondary" data-action="reload">重新加载</button>');}
}
function purchaseModal(productId, mode = 'web', draft = {}) {
  const product = array(state.catalog.products).find(p => p.id === productId); if (!product) return;
  const xianyu = mode === 'xianyu';
  showModal(`<div class="eyebrow">YOUR NEXT MOVIE NIGHT</div><h2 id="modal-title">${xianyu ? '闲鱼订单审核' : '登记购买'}</h2><p class="subtitle">${xianyu ? '已在闲鱼付款？提交订单信息，等待核对后在闲鱼交付。' : '确认套餐后创建待付款订单，到账核对后提供卡密。'}</p>
    <aside class="purchase-notice-inline" aria-label="购买必看"><strong>购买必看！</strong><p><strong>查片直接搜索，无需购买试用卡或天卡。</strong><br><strong>试用卡用于测试实际播放速度、流畅度和客户端体验。</strong></p><a href="/library">先查片库 →</a><a href="/guide#guide-library">查看完整说明 →</a></aside>
    <div class="purchase-modes" role="group" aria-label="购买方式">${[['web','网页下单'],['xianyu','闲鱼已下单']].map(([value,label])=>`<button type="button" data-action="purchase-mode" data-mode="${value}" data-product="${esc(product.id)}" aria-pressed="${(value==='xianyu')===xianyu}">${label}</button>`).join('')}</div>
    <div class="order-summary"><div><h3>${esc(productTitle(product))}</h3><p>${esc(durationLabel(product))} · ${own(product) ? '自备 115 网盘' : '负载均衡模式'} · 1 张卡</p></div><strong>¥${money(product.listed_price_cents)}</strong></div>
    ${state.catalog?.xianyu_store_url ? `<a class="purchase-shop-link" href="${esc(state.catalog.xianyu_store_url)}" target="_blank" rel="noopener noreferrer"><span>还未购买？<strong>前往闲鱼购买</strong></span>${icon('external',16)}</a>` : ''}
    ${xianyu ? `<form id="xianyu-purchase-form" data-product="${esc(product.id)}"><label class="field"><span>闲鱼订单号</span><input name="xianyu_order_id" required inputmode="numeric" pattern="[0-9]{1,80}" maxlength="80" value="${esc(draft.xianyu_order_id||'')}" placeholder="从闲鱼订单详情复制完整订单号" autocomplete="off"><small>请选择与闲鱼实付订单相同的套餐。</small></label><label class="field"><span>闲鱼昵称 / 补充说明 <span class="micro">（可选）</span></span><input name="buyer_note" maxlength="120" value="${esc(draft.buyer_note||'')}" placeholder="方便卖家核对，请勿填写账号密码" autocomplete="off"></label><div class="callout">${icon('info',16)}<span>已在闲鱼付款的订单直接提交审核，无需在网页重复付款。审核后通过原闲鱼会话交付。</span></div><p class="form-error" role="alert"></p><button class="btn btn-primary btn-full" type="submit">提交订单审核 ${icon('arrow',16)}</button><p class="micro mt-12">若闲鱼已自动发卡，请直接查看原聊天消息。</p></form>`
    : `<form id="purchase-form" data-product="${esc(product.id)}"><label class="field"><span>联系备注 <span class="micro">（可选）</span></span><input name="contact_note" maxlength="120" value="${esc(draft.contact_note||'')}" placeholder="方便核对订单的联系备注" autocomplete="off"><small>请勿填写密码、115 Cookie 或其他账号密钥。</small></label>${!state.catalog.payment_configured ? `<div class="callout">${icon('info',16)}<span>网页收款尚未配置，现在仅登记待付款订单。可以通过其它入口完成购买。</span></div>` : ''}<p class="form-error" id="purchase-error" role="alert"></p><button class="btn btn-primary btn-full" type="submit" ${state.catalog.payment_configured===true?'':'disabled'}>创建待付款订单 ${icon('arrow',16)}</button><p class="micro mt-12">创建订单不会扣款，也不会立即生成或发放卡密。</p></form>`}
`, 'purchase-modal');
  modalRoot.querySelector('.purchase-modal').purchaseDraft = draft;
}
function publicToken(id) { try { return sessionStorage.getItem(`mediadock-order-${id}`) || ''; } catch { return ''; } }
function storePublicToken(id, token) { try { sessionStorage.setItem(`mediadock-order-${id}`, token); } catch { /* Token is still shown for manual safekeeping. */ } }
const purchaseReviewStatus = status => ({pending_review:['待审核',''],approved:['审核已通过','green'],rejected:['未通过核对','red']})[status] || ['状态待确认','gray'];
function purchaseReviewBadge(status) { const [label,color]=purchaseReviewStatus(status); return `<span class="badge ${color}">${label}</span>`; }
async function renderPurchaseReview(id, initial) {
  const generation=++publicOrderGeneration;
  window.MediaDockMedia?.stop();window.MediaDockCarousel?.stop();stopShopNetworkMonitor();
  let token=initial?.access_token || publicToken(id), review=null, failure='';
  if(token)storePublicToken(id,token);
  try { if(token)review=await api(`/api/public/purchase-reviews/${encodeURIComponent(id)}`,{headers:{'X-Order-Token':token}}); }
  catch(error) {failure=errorText(error);if(initial)review=initial;else if(error.status===404){token='';try{sessionStorage.removeItem(`mediadock-order-${id}`);}catch{}}}
  if(generation!==publicOrderGeneration)return;
  const product=array(state.catalog?.products).find(p=>p.id===(review?.product_id||initial?.product_id));
  const message=review?.status==='approved' ? '订单审核已通过，请回到原闲鱼会话查看交付进度。若此前已发卡，可直接查看原消息。' : review?.status==='rejected' ? '暂未核实到相符的交易，请在原闲鱼会话联系卖家核对订单和套餐。' : '申请已进入闲鱼订单审核。卖家核对实付订单后，通过原闲鱼会话交付；无需在这里重复付款。';
  document.title='闲鱼订单审核 · '+appearance().site_name;
  const shopUrl = state.catalog?.xianyu_store_url;
  app.innerHTML=`<div class="container">${header()}<main class="page-center"><section class="claim-card purchase-review-status"><div class="claim-icon">${icon('order',26)}</div><div class="eyebrow">XIANYU ORDER REVIEW</div><h1>${review ? purchaseReviewStatus(review.status)[0] : '查询审核进度'}</h1><p class="subtitle">闲鱼购买 · 卖家核对 · 原会话交付</p>${failure ? `<div class="callout danger">${icon('info',16)}<span>${esc(failure)}</span></div>` : ''}<div class="key-value"><span>审核申请号</span><strong class="order-id">${esc(id)}</strong></div>${product ? `<div class="key-value"><span>所选套餐</span><strong>${esc(productTitle(product))}</strong></div>` : ''}${review ? `<div class="key-value"><span>当前状态</span>${purchaseReviewBadge(review.status)}</div><div class="callout mt-24">${icon('info',16)}<span>${message}</span></div>` : ''}${token ? `<div class="inline-actions mt-24"><button class="btn btn-secondary" data-action="refresh-purchase-review" data-id="${esc(id)}">${icon('refresh',15)}刷新审核进度</button>${shopUrl ? `<a class="btn btn-outline" href="${esc(shopUrl)}" target="_blank" rel="noopener noreferrer">前往闲鱼 ${icon('external',14)}</a>` : ''}</div><p class="micro mt-18">请保存本页地址和查询凭证，以便再次查询进度。</p><details class="mt-12"><summary class="micro">保存审核查询凭证</summary><div class="card-code token-code mt-12">${esc(token)}</div><button class="btn btn-outline btn-small mt-12" data-action="copy" data-copy="${esc(token)}">复制查询凭证</button></details>` : `<form id="purchase-review-access-form" data-id="${esc(id)}"><label class="field mt-24"><span>审核查询凭证</span><input name="token" required maxlength="128" autocomplete="off" placeholder="粘贴提交审核时保存的查询凭证"></label><p class="form-error" role="alert"></p><button class="btn btn-primary btn-full" type="submit">查询审核进度</button></form>`}</section></main>${footer()}</div>`;
}
function statusInfo(status) {
  return ({pending_payment:['待付款 / 待核对',''],pending_review:['等待核对到账',''],payment_reported:['买家已提交付款说明',''],awaiting_review:['等待核对到账',''],approved:['已核对到账','blue'],eligible:['可生成卡密','blue'],processing:['正在生成卡密',''],issuing:['正在生成卡密',''],issued:['卡密已生成','green'],ready:['卡密可领取','green'],delivered:['已发货','green'],claimed:['已领取','green'],needs_review:['需人工核查','red'],failed:['处理异常','red'],cancelled:['已取消','gray'],rejected:['未通过核对','red']})[status] || [status || '未知状态','gray'];
}
function badge(status) { const [label,color] = statusInfo(status); return `<span class="badge ${color}">${esc(label)}</span>`; }
async function renderPublicOrder(id, initial) {
  const generation=++publicOrderGeneration;
  window.MediaDockMedia?.stop();
  window.MediaDockCarousel?.stop();stopShopNetworkMonitor();
  let token = initial?.access_token || publicToken(id);
  if (token) storePublicToken(id, token);
  let order;
  try { order = token ? await api(`/api/public/orders/${encodeURIComponent(id)}`, {headers:{'X-Order-Token':token}}) : null; }
  catch (error) { order = initial || {error:errorText(error)}; if (!initial && error.status === 404) token = ''; }
  if(generation!==publicOrderGeneration)return;
  const productId = order?.product_id || initial?.product_id;
  const product = array(state.catalog?.products).find(p => p.id === productId);
  const claimToken = order?.claim_token || initial?.claim_token;
  const claimUrl = claimToken ? `/claim#${encodeURIComponent(claimToken)}` : safeUrl(order?.claim_url);
  const qr = state.catalog?.payment_qr || {};
  const qrHtml = ['wechat','alipay'].map(key => safeUrl(qr[key],{local:true}) ? `<div class="payment-qr"><img src="${esc(safeUrl(qr[key],{local:true}))}" alt="${key === 'wechat' ? '微信' : '支付宝'}收款码"><p class="micro mt-12">${key === 'wechat' ? '微信支付' : '支付宝'}</p></div>` : '').join('');
  document.title='订单详情 · '+appearance().site_name;
  app.innerHTML = `<div class="container">${header()}<main class="page-center"><section class="claim-card"><div class="claim-icon">${icon('order',26)}</div><div class="eyebrow">YOUR ORDER</div><h1>${token ? '订单已登记' : '查询你的订单'}</h1><p class="subtitle">付款核对与卡密领取进度，都在这里。</p>${order?.error ? `<div class="callout danger">${icon('info',16)}<span>${esc(order.error)}</span></div>` : ''}<div class="key-value"><span>订单号</span><strong class="order-id">${esc(id)}</strong></div>${product ? `<div class="key-value"><span>所选套餐</span><strong>${esc(productTitle(product))}</strong></div><div class="key-value"><span>应付金额</span><strong>¥${money(product.listed_price_cents)}</strong></div>` : ''}${order?.status ? `<div class="key-value"><span>当前状态</span>${badge(order.status)}</div>` : ''}${token ? `<div class="callout mt-24">${icon('info',16)}<span>${['issued','ready','delivered','claimed'].includes(order?.status) ? '卡密已经准备好，请通过领取入口查看。' : '登记订单不代表已经付款。付款后需要管理员核对到账，才会生成对应卡密。'}</span></div>${qrHtml && ['pending_payment','pending_review','payment_reported','awaiting_review'].includes(order?.status) ? `<div class="inline-actions mt-24">${qrHtml}</div><p class="micro mt-12">付款请备注订单号，完成后提交付款说明。</p>` : ''}${!state.catalog?.payment_configured ? '<p class="micro mt-18">网页收款方式待配置，请勿向来源不明的收款码转账。可通过闲鱼购买。</p>' : ''}${claimUrl ? `<a class="btn btn-primary btn-full mt-24" href="${esc(claimUrl)}">领取我的卡密 ${icon('key',16)}</a>` : ''}${['pending_payment','pending_review','payment_reported','awaiting_review'].includes(order?.status) && state.catalog?.payment_configured ? `<form id="paid-notice-form" data-id="${esc(id)}"><label class="field mt-24"><span>付款说明</span><textarea name="payment_note" maxlength="240" required placeholder="支付方式、时间及交易单号末几位，方便核对"></textarea></label><p class="form-error" id="paid-error" role="alert"></p><button class="btn btn-secondary btn-full" type="submit">我已付款，提交核对</button><p class="micro mt-12">此操作仅提交付款说明，等待管理员核对到账。</p></form>` : ''}<div class="inline-actions mt-24"><button class="btn btn-outline" data-action="refresh-public-order" data-id="${esc(id)}">${icon('refresh',15)}刷新状态</button><button class="btn btn-outline" data-action="copy" data-copy="${esc(id)}">${icon('copy',15)}复制订单号</button></div><p class="micro mt-18">请保存本页地址及查询凭证。关闭标签页或更换浏览器后，可能需要重新输入凭证。</p><details class="mt-12"><summary class="micro">保留订单查询凭证</summary><p class="micro mt-12">换浏览器查询时使用，请妥善保存并勿公开分享。</p><div class="card-code token-code mt-12">${esc(token)}</div><button class="btn btn-outline btn-small mt-12" data-action="copy" data-copy="${esc(token)}">复制查询凭证</button></details>` : `<form id="order-access-form" data-id="${esc(id)}"><label class="field mt-24"><span>订单查询凭证</span><input name="token" autocomplete="off" required placeholder="粘贴创建订单时保存的查询凭证"></label><button class="btn btn-primary btn-full" type="submit">查询订单</button></form>`}</section></main>${footer()}</div>`;
}
function readClaimHash() { const value = location.hash.slice(1); try { return decodeURIComponent(value.startsWith('token=') ? value.slice(6) : value); } catch { return value; } }
async function renderClaim() {
  stopShopNetworkMonitor();
  if (!state.catalog) {
    try { state.catalog = await api('/api/public/catalog', {timeout:10000}); }
    catch { /* Card lookup remains available if the catalog cannot be loaded. */ }
  }
  document.title='领取卡密 · '+appearance().site_name;
  const token = readClaimHash();
  app.innerHTML = `<div class="container">${header('claim')}<main class="page-center"><section class="claim-card" id="claim-content"><div class="claim-icon">${icon('key',27)}</div><div class="eyebrow">A LITTLE LIGHT, RESERVED FOR YOU</div><h1>你的观影时光，已在路上</h1><p class="subtitle">输入订单的领取凭证，查看属于你的卡密。<br>如果你收到的是领取链接，凭证已自动填入。</p><form id="claim-form"><label class="field"><span>领取凭证</span><textarea name="token" autocomplete="off" spellcheck="false" required placeholder="粘贴领取链接中的凭证，或完整领取链接">${esc(token)}</textarea></label><p class="form-error" id="claim-error" role="alert"></p><button class="btn btn-primary btn-full" type="submit">查看我的卡密 ${icon('arrow',16)}</button></form><div class="callout mt-24">${icon('shield',16)}<span>领取凭证是你的取卡凭据，请勿公开分享。卡密准备完成后，可用同一凭证再次查看。</span></div><p class="micro mt-18">尚未付款或正在核对的订单，需要等待处理完成。<br>已有卡密的用户可直接前往用户中心兑换。</p>${userCenterLink('已有卡密，前往用户中心', 'btn btn-secondary btn-full mt-18')}</section></main>${footer()}</div>`;
}
function parseClaimInput(value) { value = value.trim(); if (/^https?:\/\//i.test(value)) { try { const url = new URL(value); value = url.hash.slice(1); } catch { return ''; } } if (value.startsWith('token=')) value=value.slice(6); try{return decodeURIComponent(value);}catch{return value;} }
function claimSuccess(data) {
  const redeem = safeUrl(data.redemption_url);
  document.getElementById('claim-content').innerHTML = `<div class="claim-icon">${icon('check',27)}</div><div class="eyebrow">YOUR CARD IS READY</div><h1>卡密已为你准备好</h1><p class="subtitle">${esc(data.name || data.product?.name || '影视服观影卡')} · ${esc(data.duration_days ?? data.product?.duration_days ?? '—')} 天<br>复制下方卡密，在用户中心登录后兑换。</p><div class="card-code">${esc(data.card)}</div><button class="btn btn-primary btn-full mt-18" data-action="copy" data-copy="${esc(data.card)}">${icon('copy',16)}复制卡密</button>${redeem ? `<a class="btn btn-secondary btn-full mt-12" href="${esc(redeem)}" target="_blank" rel="noopener noreferrer">前往用户中心兑换 ${icon('external',15)}</a>` : '<p class="micro mt-18">用户中心地址尚未配置，请联系卖家获取。</p>'}<div class="callout success mt-24">${icon('shield',16)}<span>请保留领取链接和卡密。重复查看会显示同一张卡，不会重新生成。</span></div><a href="/plans" class="text-link mt-24">浏览其他套餐 ${icon('arrow',15)}</a>`;
}
async function renderAdmin() {
  window.MediaDockSubscriptions?.stopAdmin();
  stopShopNetworkMonitor();
  document.title='管理控制台 · '+appearance().site_name;
  try { state.auth = await api('/api/auth/status'); if (!state.auth.authenticated) return renderAuth(); await refreshOverview(); renderAdminShell(); }
  catch (error) { app.innerHTML = `<div class="container"><main class="page-center">${empty(errorText(error),'管理服务暂时不可用','<button class="btn btn-secondary" data-action="reload">重新连接</button>')}</main></div>`; }
}
function renderAuth() {
  const setup = !state.auth.initialized;
  app.innerHTML = `<main class="auth-wrap"><section class="auth-story"><a href="/">${brand()}</a><div><div class="eyebrow">MediaDock BUSINESS CONSOLE</div><h2>每一份期待，<br>都值得被好好交付。</h2><p>管理你的套餐、订单与发卡流程。<br>从购卡到交付，在一处从容完成。</p></div><span class="auth-story-footer">${esc(appearance().site_name)} · 管理控制台</span></section><section class="auth-content"><div class="auth-card"><a href="/">${brand()}</a><div class="eyebrow">${setup ? 'WELCOME TO MediaDock' : 'WELCOME BACK'}</div><h1>${setup ? '设置你的管理密码' : '欢迎回到'+esc(appearance().site_name)}</h1><p class="subtitle">${setup ? '首次使用，请创建管理员密码。此密码仅用于当前发卡网站。' : '登录后查看订单、核对付款与管理发卡。'}</p><form id="auth-form" data-mode="${setup ? 'setup' : 'login'}"><label class="field"><span>管理员用户名</span><input type="text" name="username" autocomplete="username" maxlength="64" ${setup ? 'value="admin" readonly' : ''} required placeholder="请输入管理员用户名"></label><label class="field"><span>管理密码</span><input type="password" name="password" autocomplete="${setup ? 'new-password' : 'current-password'}" ${setup ? 'minlength="12"' : ''} maxlength="256" required placeholder="${setup ? '至少 12 位，建议使用独立密码' : '请输入管理密码'}"></label>${setup ? '<label class="field"><span>再次输入密码</span><input type="password" name="confirmation" autocomplete="new-password" required minlength="12" maxlength="256" placeholder="再次确认密码"></label>' : ''}<p class="form-error" id="auth-error" role="alert"></p><button type="submit" class="btn btn-primary btn-full">${setup ? '创建密码并进入' : '登录控制台'} ${icon('arrow',16)}</button></form><a class="back-shop" href="/">${icon('arrow',13)} 返回购卡首页</a></div></section></main>`;
}
async function refreshOverview() { state.overview = await api('/api/admin/overview'); state.overview.products = array(state.overview.products); state.overview.orders = array(state.overview.orders); }
const navItems = [['overview','grid','概览'],['orders','order','订单管理'],['delivery','key','发卡'],['subscriptions','order','订阅审核'],['settings','settings','设置']];
const orderChannels = [['web','网页购买'],['xianyu','闲鱼购买'],['xiaohongshu','小红书购买']];
function normalizedOrderSource(order) {
  if(order.source === 'website') return 'web';
  return ['web','xianyu','xiaohongshu','manual'].includes(order.source) ? order.source : 'manual';
}
function orderSourceLabel(order) {
  return ({web:'网页购买',xianyu:'闲鱼购买',xiaohongshu:'小红书购买',manual:'手工登记'})[normalizedOrderSource(order)];
}
function adminOrderEntries() {
  const orders=array(state.overview?.orders), ids=new Set(orders.map(oid));
  const reviews=array(state.overview?.purchase_reviews).filter(r=>!(r.status==='approved' && r.order_id && ids.has(r.order_id))).map(r=>({...r,order_id:r.request_id,source:'xianyu',purchase_review:true}));
  return [...orders,...reviews].sort((a,b)=>new Date(b.created_at)-new Date(a.created_at));
}
function orderSourceCounts() {
  const counts = {all:0,web:0,xianyu:0,xiaohongshu:0,manual:0};
  for(const order of adminOrderEntries()) { counts.all++; counts[normalizedOrderSource(order)]++; }
  return counts;
}
function adminNavigation() {
  const counts = orderSourceCounts();
  return navItems.map(([id,ico,name]) => {
    const active = state.tab === id;
    const button = `<button data-action="admin-tab" data-tab="${id}" class="${active?'active':''}" ${active && (id!=='orders' || state.orderSource==='all')?'aria-current="page"':''} title="${name}">${icon(ico,19)}<span class="nav-label">${name}</span>${id==='orders'?`<span class="order-group-total" id="order-group-total">${counts.all}</span>`:''}</button>`;
    if(id !== 'orders') return button;
    return `<div class="order-nav-group"><div class="order-nav-heading">${button}<button class="order-groups-toggle" data-action="toggle-order-groups" aria-expanded="${state.orderGroupsExpanded}" aria-controls="order-source-nav" aria-label="展开或收起订单来源分组"><span aria-hidden="true">⌄</span></button></div><div class="order-subnav" id="order-source-nav" ${state.orderGroupsExpanded?'':'hidden'}>${orderChannels.map(([source,label])=>`<button data-action="order-source" data-source="${source}" class="${active && state.orderSource===source?'active':''}" ${active && state.orderSource===source?'aria-current="page"':''}><span>${label}</span><span class="order-source-count" data-source-count="${source}">${counts[source]}</span></button>`).join('')}</div></div>`;
  }).join('');
}
function updateOrderNavigation() {
  const counts = orderSourceCounts(), subnav = document.getElementById('order-source-nav');
  if(subnav) subnav.hidden = !state.orderGroupsExpanded;
  document.querySelector('.order-groups-toggle')?.setAttribute('aria-expanded', String(state.orderGroupsExpanded));
  const total = document.getElementById('order-group-total');
  if(total) total.textContent = counts.all;
  document.querySelectorAll('.order-subnav [data-source]').forEach(button => {
    const active = state.tab === 'orders' && state.orderSource === button.dataset.source;
    button.classList.toggle('active', active);
    if(active) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
  });
  document.querySelectorAll('[data-source-count]').forEach(count => { count.textContent = counts[count.dataset.sourceCount]; });
}
let assistantLoadTimer;
function renderAdminShell() {
  clearTimeout(assistantLoadTimer);
  app.innerHTML = `<div class="admin-layout"><aside class="sidebar"><a href="/">${brand()}</a><div class="sidebar-label">WORKSPACE</div><nav class="admin-nav" aria-label="管理导航">${adminNavigation()}</nav><div class="sidebar-bottom"><a href="/" target="_blank" rel="noopener">${icon('external',16)}<span>查看买家页面</span></a><button data-action="logout">${icon('logout',16)}<span>退出登录</span></button></div></aside><main class="admin-main"><div class="admin-topbar"><span class="breadcrumb">${esc(appearance().site_name)} <span>/</span> 管理控制台</span><a href="/" class="mobile-admin-title">${esc(appearance().site_name)}</a><div class="admin-theme-tools">${window.MediaDockTheme?.control()||''}<span class="workspace-pill"><i class="dot"></i> 发卡管理工作台</span></div></div><div id="admin-content"></div></main></div>`; renderAdminTab();
}
function renderAdminTab() {
  window.MediaDockSubscriptions?.stopAdmin();
  const target = document.getElementById('admin-content');
  if (!target) return;
  document.querySelectorAll('.admin-nav [data-tab]').forEach(button => {
    const active = button.dataset.tab === state.tab;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  updateOrderNavigation();
  target.innerHTML = ({overview:overviewPage,orders:ordersPage,delivery:deliveryPage,subscriptions:()=>window.MediaDockSubscriptions?.adminMarkup()||empty('订阅审核暂时无法加载，请刷新重试。'),settings:settingsPage}[state.tab] || overviewPage)();
  if(state.tab==='subscriptions')window.MediaDockSubscriptions?.mountAdmin(target);
  if(state.tab==='settings') {void loadNetworkMonitorSettings();void loadNetworkProbeSettings();}
}
function pageTitle(title, subtitle, action = '') { return `<div class="admin-page-title"><div><h1>${title}</h1><p>${subtitle}</p></div>${action}</div>`; }

const newOrderButton = () => `<button class="btn btn-primary" data-action="new-order">${icon('plus',16)}新增订单</button>`;
function orderProduct(order) { return order.product || state.overview.products.find(p=>p.id===order.product_id); }
function friendlyDate(value) { if(!value) return '—'; const time = new Date(typeof value === 'number' && value < 1e12 ? value*1000 : value); return Number.isNaN(time.getTime()) ? String(value) : time.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); }
function orderRows(orders) {
  return orders.map(order => { const product = orderProduct(order); return `<tr><td><div class="order-name">${esc(product ? productTitle(product) : order.product_name || order.product_id || '未匹配套餐')}</div><div class="order-meta">${order.purchase_review ? `买家提交审核 · ${esc(order.xianyu_order_id)}` : esc(oid(order))}</div><div class="order-mobile-source">${esc(orderSourceLabel(order))}</div></td><td>${order.purchase_review ? purchaseReviewBadge(order.status) : badge(order.status)}</td><td><div class="order-name">${esc(orderSourceLabel(order))}</div><div class="order-meta">${esc(friendlyDate(order.created_at))}</div></td><td><button class="btn btn-outline btn-small" data-action="${order.purchase_review ? 'purchase-review-detail' : 'order-detail'}" data-id="${esc(oid(order))}">${order.purchase_review ? '审核' : '查看'} ${icon('arrow',12)}</button></td></tr>`; }).join('');
}
function orderTable(orders, emptyMessage = '暂无订单。买家登记购买或手工录入后会显示在这里。') { return orders.length ? `<div class="table-scroll"><table class="data-table"><thead><tr><th>套餐 / 订单</th><th>处理状态</th><th>来源 / 创建时间</th><th>操作</th></tr></thead><tbody>${orderRows(orders)}</tbody></table></div>` : `<div class="empty-inline">${esc(emptyMessage)}</div>`; }
function connectionStatus(connection, label = '') {
  if(connection?.status === 'account_bound') return '<span class="badge">账号已绑定 · 待实单验证</span>';
  if(connection?.status === 'connected' || connection?.status === 'ok' || connection?.status === 'online') return '<span class="badge green">连接已验证</span>';
  if(connection?.status === 'error' || connection?.status === 'failed') return '<span class="badge red">连接异常</span>';
  if(connection?.configured) return '<span class="badge">已配置 · 待验证</span>';
  return `<span class="badge gray">${label || '未配置'}</span>`;
}
function overviewPage() {
  const orders = adminOrderEntries(), conn = state.overview.connections || {};
  const pending = orders.filter(o=>['pending_payment','pending_review','payment_reported','awaiting_review'].includes(o.status)).length;
  const issued = orders.filter(o=>['issued','ready','delivered','claimed'].includes(o.status)).length;
  const review = orders.filter(o=>['needs_review','failed'].includes(o.status)).length;
  const stats = [['累计订单',orders.length,'所有来源的订单','order'],['等待付款核对',pending,'核对实际到账后再发卡','clock'],['卡密已生成',issued,'包含待发送与已交付订单','key'],['需要关注',review,'处理结果不确定或异常','shield']];
  return `${pageTitle('工作台概览','把每一笔订单，清楚地交付。',newOrderButton())}<div class="stats-grid">${stats.map(([label,value,foot,ico])=>`<div class="stat"><div class="stat-top"><span>${label}</span>${icon(ico,18)}</div><div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`).join('')}</div>${review ? `<div class="callout danger status-banner">${icon('info',17)}<span>有 ${review} 笔订单需要核查。请先确认 NextEmby 的生成记录和闲鱼实际发送结果，再处理这些订单，避免重复发卡。</span></div>`:''}<div class="dashboard-columns"><div><section class="panel"><div class="panel-head"><div><h2>最近订单</h2><p>付款核对、生成与交付进度</p></div><button class="btn btn-outline btn-small" data-action="admin-tab" data-tab="orders">全部订单 ${icon('arrow',12)}</button></div>${orderTable(orders.slice(0,6))}</section><section class="panel"><div class="panel-head"><h2>当前套餐</h2><span class="micro">${state.overview.products.length} 个套餐 · 每单 1 张卡</span></div>${mappingTable()}</section></div><div><section class="panel"><div class="panel-head"><h2>服务连接</h2>${icon('link',16)}</div><div class="panel-body"><div class="connection"><div class="connection-header"><span>NextEmby</span>${connectionStatus(conn.nextemby)}</div><p>负责按模板生成兑换卡密。使用独立智能体接口连接。</p><button class="btn btn-outline btn-small" data-action="check-nextemby">${icon('refresh',12)}检查连接</button></div><div class="connection"><div class="connection-header"><span>闲鱼发卡</span>${connectionStatus(conn.xianyu,'待扫码接入')}</div><p>登录闲鱼并绑定商品与规格后，才能核对订单并交付卡密。</p><button class="text-link text-button" data-action="admin-tab" data-tab="xianyu">前往接入设置 ${icon('arrow',13)}</button></div><div class="connection"><div class="connection-header"><span>网页收款</span><span class="badge ${(state.overview.settings?.payment_wechat_url || state.overview.settings?.payment_alipay_url) ? 'green' : 'gray'}">${(state.overview.settings?.payment_wechat_url || state.overview.settings?.payment_alipay_url) ? '已配置' : '待配置'}</span></div><p>支持后续设置微信或支付宝收款码。买家付款后，由管理员核对到账。</p></div></div></section><section class="panel"><div class="panel-head"><h2>发卡流程</h2></div><div class="panel-body"><div class="next-step"><span>01</span><div><h3>核对付款与套餐</h3><p>确认订单、金额和实际到账记录。</p></div></div><div class="next-step"><span>02</span><div><h3>生成一张对应卡密</h3><p>同一订单保留同一张卡，避免重复生成。</p></div></div><div class="next-step"><span>03</span><div><h3>直接发送或链接领取</h3><p>复制发货内容到闲鱼，或提供专属领取链接。</p></div></div></div></section></div></div>`;
}
function mappingTable() {
  return `<div class="table-scroll"><table class="data-table mapping-table"><thead><tr><th>套餐</th><th>价格</th><th>模板</th><th>时长</th></tr></thead><tbody>${sortedProducts(state.overview.products).map(p=>`<tr><td>${esc(productTitle(p))}</td><td>¥${money(p.listed_price_cents)}</td><td><span class="code-inline">${esc(p.template_name || '—')}</span></td><td>${esc(p.duration_days)} 天</td></tr>`).join('')}</tbody></table></div>`;
}
function filteredOrders() { const needle=state.search.trim().toLowerCase(); return adminOrderEntries().filter(o=>(state.orderSource==='all' || normalizedOrderSource(o)===state.orderSource) && (!state.status || o.status === state.status) && (!needle || [oid(o),o.xianyu_order_id,o.buyer_note,o.product_id,orderProduct(o)?.name,orderSourceLabel(o)].join(' ').toLowerCase().includes(needle))); }
function orderChannelFilters() {
  const counts = orderSourceCounts();
  return `<div class="order-channel-filters" role="group" aria-label="按购买来源筛选">${[['all','全部订单'],...orderChannels].map(([source,label])=>`<button data-action="order-source" data-source="${source}" class="${state.orderSource===source?'active':''}" aria-pressed="${state.orderSource===source}">${label}<span>${counts[source]}</span></button>`).join('')}</div>`;
}
function orderResults() {
  const orders = filteredOrders(), counts = orderSourceCounts();
  const label = state.orderSource==='all' ? '全部订单' : orderChannels.find(([source])=>source===state.orderSource)?.[1] || '全部订单';
  const emptyMessage = state.search.trim() || state.status ? '没有符合当前筛选条件的订单，可以调整状态或搜索内容。'
    : state.orderSource==='xiaohongshu' ? '暂无小红书订单。可通过“新增订单”手工登记，核对实际到账后发卡。'
    : `暂无${state.orderSource==='all'?'':label}订单。买家登记购买或手工录入后会显示在这里。`;
  return `<div class="order-result-summary"><span>${label}</span><span>显示 ${orders.length} 笔 / 共 ${counts[state.orderSource]} 笔</span></div>${orderTable(orders, emptyMessage)}`;
}
function selectOrderSource(source) {
  if(!['all',...orderChannels.map(([id])=>id)].includes(source)) return;
  state.orderSource = source;
  state.orderGroupsExpanded = true;
  state.tab = 'orders';
  renderAdminTab();
}
function ordersPage() {
  return `${pageTitle('订单管理','汇总网页、闲鱼、小红书及手工登记订单，按来源核对与交付。',newOrderButton())}${orderChannelFilters()}${state.orderSource==='xiaohongshu'?'<p class="order-channel-note">小红书订单目前可手工登记，核对实际到账后再发卡。</p>':''}<section class="panel"><div class="filter-bar"><label class="search-input">${icon('search',16)}<span class="visually-hidden">搜索订单号或备注</span><input class="input" id="order-search" value="${esc(state.search)}" placeholder="搜索订单号、套餐或买家备注"></label><label><span class="visually-hidden">按状态筛选</span><select class="input filter-select" id="order-status-filter"><option value="">全部状态</option>${['pending_payment','pending_review','payment_reported','approved','eligible','processing','issued','delivered','claimed','needs_review','failed','rejected'].map(s=>`<option value="${s}" ${state.status === s?'selected':''}>${esc(statusInfo(s)[0])}</option>`).join('')}</select></label><button class="btn btn-outline btn-small" data-action="refresh-admin" aria-label="刷新订单">${icon('refresh',15)}</button></div><div id="order-table-content">${orderResults()}</div></section><p class="micro mt-18">未指定渠道的手工订单保留在全部订单中。“卡密已生成”不代表消息已发送，发送结果不确定时先核查原订单。</p>`;
}
function bindingForms() {
  return `<div class="binding-intro"><p class="micro">下面填写实际商品 ID；规格名称已按确认的套餐固定。只有完成绑定并勾选的套餐，才允许连接器申请发卡。</p></div>${sortedProducts(state.overview.products).map(p=>`<details class="binding-details"><summary><span>${esc(productTitle(p))}</span><span class="badge ${p.enabled?'green':'gray'}">${p.enabled?'已允许连接器发卡':'未启用'}</span></summary><form id="binding-form-${esc(p.id)}" class="binding-form" data-product="${esc(p.id)}"><p class="micro mb-20">固定规格：${esc(p.xianyu_spec_name||'颜色')} / ${esc(p.xianyu_spec_value||'未配置')}</p><div class="two-col"><label class="field"><span>商品 ID <span class="micro">（多个用逗号分隔）</span></span><input name="xianyu_item_id" value="${esc(p.xianyu_item_id||'')}" maxlength="100" placeholder="1089607470736,1075969281683"><small>可绑定多个同规格商品；每笔订单只匹配其中一个商品。</small></label><label class="field"><span>原生规格 ID <span class="micro">（可选）</span></span><input name="xianyu_sku_id" value="${esc(p.xianyu_sku_id||'')}" maxlength="100" placeholder="有原生 SKU ID 时填写"></label></div><label class="checkbox-label"><input type="checkbox" name="enabled" ${p.enabled?'checked':''}>允许连接器按已付款订单为此规格发卡</label><p class="form-error mt-12" role="alert"></p><button class="btn btn-secondary btn-small mt-18" type="submit">保存此规格绑定</button></form></details>`).join('')}`;
}

const networkProbe = {config:null,nodes:[],loaded:false};
function validNetworkDisplayName(value) {
  if(typeof value!=='string') return false;
  const name=value.trim(),normalized=name.normalize('NFKC');
  return [...name].length>=1 && [...name].length<=40 && !/[\p{C}]/u.test(name) && !/[\/\\@:<>{}\[\]]/.test(normalized) && !/[\p{L}\p{N}_][.。．][\p{L}\p{N}_]/u.test(normalized);
}
function networkProbeOptions(selected='') {
  const options=['<option value="">请选择探针节点（留空则不监测）</option>'];
  if(selected && !networkProbe.nodes.some(node=>node.id===selected)) options.push(`<option value="${esc(selected)}" selected>${networkProbe.loaded?'已选节点不在当前列表，请重新选择':'已选探针（正在读取列表）'}</option>`);
  for(const node of networkProbe.nodes) options.push(`<option value="${esc(node.id)}" ${node.id===selected?'selected':''}>${esc(node.name)} · ${node.online?'在线':'离线'}</option>`);
  return options.join('');
}
const networkMonitorEditor={groups:[],revision:'',status:null,undo:[],dirty:false};
function networkMonitorDraftFromResult(result) {
  if(!result || (!Array.isArray(result.groups) && !Array.isArray(result.nodes))) throw new Error('Unavailable settings');
  const groups=Array.isArray(result.groups)?result.groups:shopNetworkGroups.map(group=>({...group,nodes:group.nodes.map(node=>({...node,...result.nodes.find(saved=>saved?.id===node.id)}))}));
  networkMonitorEditor.groups=groups.map(group=>({id:group.id,name:group.name,nodes:group.nodes.map(node=>{
    const method=['http','icmp','mmwx','browser'].includes(node.method)?node.method:'http';
    const target=typeof node.target==='string'?node.target:typeof node.url==='string'?node.url:typeof node.urlCompat==='string'?node.urlCompat:'';
    return {id:node.id,name:node.name,method,directTarget:method==='mmwx'?'':target,probeTarget:method==='mmwx'?target:''};
  })}));
  networkMonitorEditor.revision=typeof result.revision==='string'?result.revision:'';
  networkMonitorEditor.status=result.status?networkStatusData(result.status):{groups:[]};
  networkMonitorEditor.undo=[];networkMonitorEditor.dirty=false;
}
function captureNetworkMonitorDraft(form=document.getElementById('network-monitor-form')) {
  if(!form) return;
  for(const group of networkMonitorEditor.groups) {
    const title=form.elements[`group-name-${group.id}`];if(title) group.name=title.value;
    for(const node of group.nodes) {
      const name=form.elements[`name-${node.id}`],method=form.elements[`method-${node.id}`],direct=form.elements[node.id],probe=form.elements[`probe-${node.id}`];
      if(name) node.name=name.value;if(method) node.method=method.value;if(direct) node.directTarget=direct.value;if(probe) node.probeTarget=probe.value;
    }
  }
}
function networkMonitorTargetHelp(method) {
  if(method==='browser') return '此地址将在买家浏览器中可见，页面只显示项目标题。建议使用 CDN 专用测速入口或小型静态资源，支持 HEAD、直接响应且不跳转，避免使用动态首页。每轮预热 1 次，再测 3 次取中位数，至少 2 次有效响应才显示数值；绕过浏览器缓存，不携带账号 Cookie。结果包含服务处理耗时，无法保证仅测到 CDN 边缘。修改测速域名后，已打开的买家页面需刷新。';
  if(method==='mmwx') return '从探针列表中选择；买家仅看到项目标题、在线状态和探针上报的平均延迟。';
  if(method==='icmp') return '仅填写裸 IP 或域名，不带协议、端口或路径；由服务端 Ping。';
  return '可包含健康检查路径，不带账号密码、查询参数或片段；由服务端请求，地址不公开。';
}
function networkMonitorSettingsMarkup(result) {
  if(result) networkMonitorDraftFromResult(result);
  const groups=networkMonitorEditor.groups,nodeCount=groups.reduce((sum,group)=>sum+group.nodes.length,0);
  return `<form id="network-monitor-form" autocomplete="off"><fieldset class="network-settings-fields"><legend class="sr-only">自定义线路监测</legend><div class="network-editor-toolbar"><p class="micro">直接修改标题；分组与项目均可自由添加、删除。保存后才会更新买家页面。</p><button type="button" class="btn btn-outline btn-small" data-action="network-group-add" ${groups.length>=12?'disabled':''}>${icon('plus',14)}添加分组</button></div>${networkMonitorEditor.undo.length?`<div class="network-editor-undo" role="status"><span>已从草稿移除。仅在保存后移除本站展示与监测，不影响探针或服务器。</span><button type="button" class="btn btn-outline btn-small" data-action="network-monitor-undo">撤销删除</button></div>`:''}${groups.map(group=>`<section class="network-settings-group" data-network-group="${esc(group.id)}"><div class="network-settings-group-heading"><label class="network-editable-heading"><span class="visually-hidden">分组标题</span><input type="text" name="group-name-${esc(group.id)}" value="${esc(group.name)}" maxlength="40" required placeholder="填写分组标题" aria-label="分组标题" autocomplete="off"></label><button type="button" class="btn btn-outline btn-small network-remove-button" data-action="network-group-remove" data-group="${esc(group.id)}" aria-label="删除此分组">删除分组</button></div><p class="micro network-title-help">上方为分组标题，下方为项目标题，点击即可修改。</p>${group.nodes.map(node=>{
    const method=node.method,status=networkMonitorEditor.status?.groups.flatMap(group=>group.nodes).find(item=>item.id===node.id)?.status || 'unconfigured';
    const [baseLabel,tone]=shopNetworkStatuses[status];
    const label=method==='browser' && status==='client_pending'?'由访客浏览器测量':method==='mmwx'?({healthy:'心跳正常',degraded:'心跳正常',down:'心跳离线',unknown:'心跳未知'}[status]||baseLabel):baseLabel;
    return `<section class="network-settings-node" data-network-node="${esc(node.id)}"><div class="network-settings-node-heading"><label class="network-editable-heading"><span class="visually-hidden">监测项目标题</span><input type="text" name="name-${esc(node.id)}" value="${esc(node.name)}" maxlength="40" required placeholder="填写监测项目标题" aria-label="监测项目标题" autocomplete="off"></label><span class="network-settings-node-state ${tone}">${label}</span><button type="button" class="btn btn-outline btn-small network-remove-button" data-action="network-node-remove" data-node="${esc(node.id)}" aria-label="删除此监测项目">删除</button></div><div class="network-settings-node-controls"><label class="field"><span>监测方式</span><select name="method-${esc(node.id)}" data-network-method="${esc(node.id)}"><option value="http" ${method==='http'?'selected':''}>服务端 HTTP / HTTPS</option><option value="browser" ${method==='browser'?'selected':''}>买家浏览器测速（CDN）</option><option value="icmp" ${method==='icmp'?'selected':''}>Ping（ICMP）</option><option value="mmwx" ${method==='mmwx'?'selected':''}>妙妙屋 X 探针</option></select></label><label class="field" data-network-direct-field ${method==='mmwx'?'hidden':''}><span data-network-target-label>${method==='icmp'?'IP / 域名':method==='browser'?'公开 HTTPS 测速地址':'HTTP / HTTPS 地址'}</span><input type="${method==='icmp'?'text':'url'}" name="${esc(node.id)}" value="${esc(node.directTarget)}" maxlength="${method==='icmp'?253:2048}" placeholder="${method==='icmp'?'填写裸 IP 或域名':'https://…'}" autocomplete="off" spellcheck="false" ${method==='mmwx'?'disabled':''}></label><label class="field" data-network-probe-field ${method==='mmwx'?'':'hidden'}><span>选择探针节点 <span class="micro">仅后台显示真实名称</span></span><select name="probe-${esc(node.id)}" data-network-probe-select ${method==='mmwx'?'':'disabled'}>${networkProbeOptions(node.probeTarget)}</select></label></div><p class="network-settings-target-help${method==='browser'?' is-public':''}" data-network-target-help>${networkMonitorTargetHelp(method)} 留空则不监测。</p>${method==='browser'?'<p class="micro">本设置页不运行测速。<a href="/status" target="_blank" rel="noopener noreferrer">打开服务状态页实测 ↗</a>。测速地址若跳转到其他域名，浏览器会阻止请求；请使用直接响应的专用测速地址。</p>':''}${groups.length>1?`<label class="network-group-move"><span>所属分组</span><select data-network-move="${esc(node.id)}" aria-label="移动监测项目到分组">${groups.map(option=>`<option value="${esc(option.id)}" ${option.id===group.id?'selected':''}>${esc(option.name||'未命名分组')}</option>`).join('')}</select></label>`:''}</section>`;
  }).join('')||'<p class="network-editor-empty">此分组还没有监测项目。</p>'}<button type="button" class="btn btn-outline btn-small" data-action="network-node-add" data-group="${esc(group.id)}" ${nodeCount>=40?'disabled':''}>${icon('plus',14)}添加监测项目</button></section>`).join('')||'<p class="network-editor-empty">尚未配置监测。添加分组后，即可添加浏览器测速、HTTP、Ping 或探针项目。</p>'}<p class="micro network-editor-limit">最多 12 个分组、40 个项目。标题会对买家公开，请勿填写 IP、域名或网址。</p><p class="form-error" role="alert"></p><p class="network-settings-feedback" role="status">${networkMonitorEditor.dirty?'有未保存的更改。':''}</p><div class="inline-actions"><span class="micro">${groups.length} 个分组 · ${nodeCount} 个项目</span><button class="btn btn-primary" type="submit">保存监测设置 ${icon('check',16)}</button></div></fieldset></form>`;
}
function renderNetworkMonitorDraft(focusName='') {
  const root=document.getElementById('network-monitor-settings');if(!root) return;
  root.innerHTML=networkMonitorSettingsMarkup();
  if(focusName) root.querySelector('form')?.elements[focusName]?.focus();
}
function networkMonitorEditorAction(action,button) {
  captureNetworkMonitorDraft();
  const groups=networkMonitorEditor.groups;
  const newId=prefix=>`${prefix}-${crypto.randomUUID().replaceAll('-','')}`;
  let focusName='';
  if(action==='network-group-add') {
    if(groups.length>=12) return;
    const id=newId('g');groups.push({id,name:'新监测分组',nodes:[]});focusName=`group-name-${id}`;
  } else if(action==='network-node-add') {
    const group=groups.find(group=>group.id===button.dataset.group);
    if(!group || groups.reduce((sum,item)=>sum+item.nodes.length,0)>=40) return;
    const id=newId('n');group.nodes.push({id,name:'新监测项目',method:'http',directTarget:'',probeTarget:''});focusName=`name-${id}`;
  } else if(action==='network-group-remove') {
    const index=groups.findIndex(group=>group.id===button.dataset.group);if(index<0) return;
    networkMonitorEditor.undo.push({type:'group',index,item:groups.splice(index,1)[0]});
  } else if(action==='network-node-remove') {
    const group=groups.find(group=>group.nodes.some(node=>node.id===button.dataset.node));if(!group) return;
    const index=group.nodes.findIndex(node=>node.id===button.dataset.node);
    networkMonitorEditor.undo.push({type:'node',groupId:group.id,index,item:group.nodes.splice(index,1)[0]});
  } else if(action==='network-monitor-undo') {
    const removed=networkMonitorEditor.undo.at(-1);if(!removed) return;
    const count=groups.reduce((sum,group)=>sum+group.nodes.length,0),group=groups.find(group=>group.id===removed.groupId);
    if((removed.type==='group' && (groups.length>=12 || count+removed.item.nodes.length>40)) || (removed.type==='node' && (!group || count>=40))) {toast('请先减少分组或项目，再撤销删除。',true);return;}
    networkMonitorEditor.undo.pop();
    if(removed.type==='group') groups.splice(Math.min(removed.index,groups.length),0,removed.item);
    else group.nodes.splice(Math.min(removed.index,group.nodes.length),0,removed.item);
  } else return;
  networkMonitorEditor.dirty=true;renderNetworkMonitorDraft(focusName);
}
function moveNetworkMonitorNode(select) {
  captureNetworkMonitorDraft();
  const groups=networkMonitorEditor.groups,source=groups.find(group=>group.nodes.some(node=>node.id===select.dataset.networkMove)),target=groups.find(group=>group.id===select.value);
  if(!source || !target || source===target) return;
  target.nodes.push(source.nodes.splice(source.nodes.findIndex(node=>node.id===select.dataset.networkMove),1)[0]);
  networkMonitorEditor.dirty=true;renderNetworkMonitorDraft(`name-${select.dataset.networkMove}`);
}
function changeNetworkMonitorMethod(select) {
  const nodeId=select.dataset.networkMethod;
  const row=select.closest('.network-settings-node'),input=select.form?.elements[nodeId],probe=select.form?.elements[`probe-${nodeId}`];
  if(!row || !input || !probe) return;
  const isPing=select.value==='icmp',isProbe=select.value==='mmwx',isBrowser=select.value==='browser';
  input.disabled=isProbe;probe.disabled=!isProbe;
  row.querySelector('[data-network-direct-field]').hidden=isProbe;
  row.querySelector('[data-network-probe-field]').hidden=!isProbe;
  if(!isProbe) {
    input.type=isPing?'text':'url';input.maxLength=isPing?253:2048;input.placeholder=isPing?'填写裸 IP 或域名':'https://…';
    row.querySelector('[data-network-target-label]').textContent=isPing?'IP / 域名':isBrowser?'公开 HTTPS 测速地址':'HTTP / HTTPS 地址';
  }
  row.querySelector('[data-network-target-help]').textContent=networkMonitorTargetHelp(select.value)+' 留空则不监测。';
  row.querySelector('[data-network-target-help]').classList.toggle('is-public',isBrowser);
  const badge=row.querySelector('.network-settings-node-state');badge.className='network-settings-node-state';badge.textContent='更改待保存';
  networkMonitorEditor.dirty=true;captureNetworkMonitorDraft(select.form);
}
function networkProbeSettingsMarkup(config) {
  return `<form id="network-probe-form" autocomplete="off"><fieldset class="network-settings-fields"><legend class="sr-only">妙妙屋 X 探针连接</legend><label class="field"><span>妙妙屋 X 地址</span><input type="url" name="url" value="${esc(config.url||'')}" placeholder="https://probe.example.com" autocomplete="off" spellcheck="false"><small>填写 HTTPS 主地址，留空保存可停用接入。该地址仅管理员可见。</small></label><p class="form-error" role="alert"></p><p class="network-probe-feedback" role="status"></p><div class="inline-actions"><span class="micro" data-network-probe-status></span><div class="network-probe-actions"><button class="btn btn-outline btn-small" data-action="network-probe-refresh" type="button">${icon('refresh',13)}刷新探针列表</button><button class="btn btn-secondary btn-small" type="submit">保存探针连接</button></div></div></fieldset></form>`;
}
function updateNetworkProbeResult(result) {
  if(!result || !Array.isArray(result.nodes)) throw new Error('Unavailable probe list');
  networkProbe.config={url:typeof result.url==='string'?result.url:'',configured:Boolean(result.configured),status:result.status,checked_at:result.checked_at};
  networkProbe.nodes=result.nodes.filter(node=>node && typeof node.id==='string' && /^[a-f0-9]{64}$/.test(node.id) && typeof node.name==='string').map(node=>({id:node.id,name:node.name.slice(0,200),online:node.online===true}));
  networkProbe.loaded=true;
  for(const select of document.querySelectorAll('[data-network-probe-select]')) {const selected=select.value;select.innerHTML=networkProbeOptions(selected);}
  updateNetworkProbeStatus();
}
function updateNetworkProbeStatus() {
  const root=document.getElementById('network-probe-settings');
  if(!root) return;
  const config=networkProbe.config,status=root.querySelector('[data-network-probe-status]');
  if(status) status.textContent=config?.status==='ready'?`已读取 ${networkProbe.nodes.length} 个探针节点`:config?.configured?'暂未读取到探针列表，可刷新重试':'尚未接入探针';
  const refresh=root.querySelector('[data-action="network-probe-refresh"]');
  if(refresh) refresh.disabled=!config?.configured;
}
async function loadNetworkProbeSettings() {
  const root=document.getElementById('network-probe-settings');
  if(!root || state.tab!=='settings') return;
  networkProbe.config=null;networkProbe.nodes=[];networkProbe.loaded=false;
  root.innerHTML='<p class="micro" role="status">正在读取探针连接…</p>';
  try {
    const result=await api('/api/admin/network-probe');
    if(root.isConnected && state.tab==='settings') {
      root.innerHTML=networkProbeSettingsMarkup(result);updateNetworkProbeResult(result);
    }
  } catch {
    if(root.isConnected && state.tab==='settings') root.innerHTML='<p class="form-error" role="alert">探针连接暂时无法读取，请重新登录或稍后再试。</p><button class="btn btn-outline btn-small" data-action="network-probe-reload" type="button">重新读取</button>';
  }
}
async function refreshNetworkProbe(button) {
  const form=button.closest('form'),error=form.querySelector('.form-error'),feedback=form.querySelector('.network-probe-feedback');
  error.textContent='';feedback.textContent='';
  const release=busy(button),fields=form.querySelector('fieldset');fields.disabled=true;
  try {
    const result=await api('/api/admin/network-probe/refresh',{method:'POST',body:{},timeout:20000});
    if(form.isConnected && state.tab==='settings') {updateNetworkProbeResult(result);feedback.textContent=result.status==='ready'?'探针列表已刷新，下方未保存的内容已保留。':'探针列表暂不可用，请检查连接地址后重试。';}
  } catch {if(error.isConnected) error.textContent='探针列表未能刷新，请检查连接地址并稍后重试。';}
  finally {fields.disabled=false;release();updateNetworkProbeStatus();}
}
async function saveNetworkProbeSettings(form) {
  const error=form.querySelector('.form-error'),feedback=form.querySelector('.network-probe-feedback');
  error.textContent='';feedback.textContent='';
  let url=form.elements.url.value.trim();
  if(url) {
    try {
      const parsed=new URL(url);
      if(parsed.protocol!=='https:' || parsed.username || parsed.password || !['','/'].includes(parsed.pathname) || parsed.search || parsed.hash || /[?#]/.test(url)) throw new Error('Invalid URL');
      url=parsed.origin;
    } catch {error.textContent='请填写 HTTPS 主地址，不包含账号密码、路径、查询参数或片段。';return;}
  }
  const release=busy(form.querySelector('[type="submit"]')),fields=form.querySelector('fieldset');fields.disabled=true;
  try {
    const result=await api('/api/admin/network-probe',{method:'POST',body:{url},timeout:20000});
    if(form.isConnected && state.tab==='settings') {form.elements.url.value=result.url||'';updateNetworkProbeResult(result);feedback.textContent=url?'探针连接已保存，可刷新列表后选择下方节点。':'探针接入已停用，原有节点选择已保留。';}
  } catch {if(error.isConnected) error.textContent='探针连接未能保存，请检查地址格式并稍后重试。';}
  finally {fields.disabled=false;release();updateNetworkProbeStatus();}
}
function validNetworkPingTarget(target) {
  if(!target || target.length>253 || /[\s\\/?#@%\[\]]/.test(target)) return false;
  try {
    if(target.includes(':')) return /^[0-9a-f:.]+$/i.test(target) && new URL(`http://[${target}]/`).hostname.startsWith('[');
    return target.replace(/\.$/,'').split('.').every(label=>/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label));
  } catch {return false;}
}
async function loadNetworkMonitorSettings() {
  const root=document.getElementById('network-monitor-settings');
  if(!root || state.tab!=='settings') return;
  root.innerHTML='<p class="micro" role="status">正在读取监测设置…</p>';
  try {
    const result=await api('/api/admin/network-monitor');
    if(root.isConnected && state.tab==='settings') root.innerHTML=networkMonitorSettingsMarkup(result);
  } catch {
    if(root.isConnected && state.tab==='settings') root.innerHTML='<p class="form-error" role="alert">监测设置暂时无法读取，请重新登录或稍后再试。</p><button class="btn btn-outline btn-small mt-12" data-action="network-monitor-reload" type="button">重新读取</button>';
  }
}
async function saveNetworkMonitorSettings(form) {
  const error=form.querySelector('.form-error'),feedback=form.querySelector('.network-settings-feedback');
  error.textContent='';feedback.textContent='';
  captureNetworkMonitorDraft(form);
  const groups=networkMonitorEditor.groups.map(group=>({id:group.id,name:group.name.trim(),nodes:group.nodes.map(node=>({id:node.id,name:node.name.trim(),method:node.method,target:(node.method==='mmwx'?node.probeTarget:node.directTarget).trim()}))}));
  const nodes=groups.flatMap(group=>group.nodes);
  if(groups.length>12 || nodes.length>40) {error.textContent='最多可添加 12 个分组、40 个监测项目。';return;}
  if([...groups,...nodes].some(item=>!validNetworkId(item.id) || !validNetworkDisplayName(item.name))) {error.textContent='分组与项目标题请填写 1–40 字，不要包含 IP、域名、网址或特殊标记。';return;}
  for(const node of nodes) {
    if(!['http','icmp','mmwx','browser'].includes(node.method)) {error.textContent='请选择有效的监测方式。';return;}
    if(!node.target) continue;
    if(node.method==='mmwx') {
      if(!/^[a-f0-9]{64}$/.test(node.target)) {error.textContent='请从探针列表中选择节点。';return;}
    } else if(node.method==='browser') {
      if(!validBrowserMonitorUrl(node.target)) {error.textContent='浏览器测速请填写 HTTPS 地址，不含账号、密码、查询参数或片段；该测速地址将在买家浏览器中可见。';return;}
    } else if(node.method==='icmp') {
      if(!validNetworkPingTarget(node.target)) {error.textContent='Ping 请填写裸 IP 或域名，不带协议、端口、路径、账号密码或参数。';return;}
    } else {
      try {
        const url=new URL(node.target);
        if(!['http:','https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || node.target.includes('?') || node.target.includes('#')) throw new Error('Invalid target');
      } catch {
        error.textContent='HTTP / HTTPS 请填写完整地址，可包含健康检查路径，但不要包含账号、密码、查询参数或片段。';
        return;
      }
    }
  }
  const button=form.querySelector('[type="submit"]'),release=busy(button);
  const fields=form.querySelector('fieldset');fields.disabled=true;
  try {
    const result=await api('/api/admin/network-monitor',{method:'POST',body:{version:2,revision:networkMonitorEditor.revision,groups}});
    if(form.isConnected && state.tab==='settings') {
      const root=document.getElementById('network-monitor-settings');
      root.innerHTML=networkMonitorSettingsMarkup(result);
      root.querySelector('.network-settings-feedback').textContent='监测设置已保存，将在下一轮监测中更新。';
    }
  } catch (failure) {
    if(error.isConnected) error.textContent=failure.status===409?'监测配置已在其他页面更新。你的草稿仍已保留；请先记录所需更改，再刷新页面读取最新配置，避免覆盖。':'监测设置未能保存，请检查标题、地址格式和探针选择后重试。';
  } finally {fields.disabled=false;release();}
}
function introductionSectionFields(section = {title:'',body:''}) {
  return `<fieldset class="intro-editor-section"><legend>公告段落</legend><label class="field"><span>段落标题</span><input data-intro-section-title required maxlength="160" value="${esc(section.title)}"></label><label class="field"><span>正文</span><textarea data-intro-section-body required maxlength="4000" rows="5">${esc(section.body)}</textarea></label><button type="button" class="btn btn-outline btn-small" data-action="intro-remove-section">删除此段</button></fieldset>`;
}
function introductionSettingsPanel() {
  const intro=state.overview?.settings?.introduction || DEFAULT_INTRODUCTION;
  return `<form id="introduction-form" class="panel network-settings-panel"><section class="settings-section"><h2>首页公告设置</h2><p class="subtitle">编辑首页公告、入口名称和正文。保存后生效，预览不会发布。</p><label class="field"><span>首页入口名称</span><input name="entry_label" required maxlength="24" value="${esc(intro.entry_label || '影视服简介')}" placeholder="例如：影视服简介、购买必看、站点公告"><small>显示在首页及各页面底部，位于「观影套餐」前面。</small></label><label class="checkbox-label"><input type="checkbox" name="enabled" ${intro.enabled?'checked':''}>首页首次访问自动弹出</label><p class="micro mt-12">自动弹出后等待 1 秒可点击「了解」。同一浏览器看过后不重复弹出；修改内容不会清除已读记录。关闭自动弹出后，页脚仍可查看。</p><label class="field mt-18"><span>公告标题</span><input name="title" required maxlength="120" value="${esc(intro.title)}"></label><label class="field"><span>简短说明（选填）</span><input name="subtitle" maxlength="240" value="${esc(intro.subtitle)}"></label><div data-intro-sections>${intro.sections.map(introductionSectionFields).join('')}</div><button type="button" class="btn btn-outline btn-small" data-action="intro-add-section">${icon('plus',14)}添加段落</button><p class="micro mt-12">支持换行；标题含「购买必看」的段落会重点显示。最多 12 段，正文合计最多 16000 字。公告面向所有访客公开，请勿填写密码、密钥或内部服务地址。</p><p class="form-error" role="alert"></p><div class="inline-actions mt-18"><button type="button" class="btn btn-outline" data-action="intro-preview">预览公告</button><button type="submit" class="btn btn-primary">保存公告 ${icon('check',16)}</button></div></section></form>`;
}
function introductionFormValue(form) {
  return {entry_label:form.elements.entry_label.value.trim(),enabled:form.elements.enabled.checked,title:form.elements.title.value.trim(),subtitle:form.elements.subtitle.value.trim(),sections:[...form.querySelectorAll('.intro-editor-section')].map(row=>({title:row.querySelector('[data-intro-section-title]').value.trim(),body:row.querySelector('[data-intro-section-body]').value.trim()}))};
}
async function openPublicIntroduction() {
  try {const catalog=await api('/api/public/catalog',{timeout:15000});state.catalog=catalog;showIntroduction();}
  catch(error){toast(errorText(error),true);}
}
const settingsCategories = [['site','站点与入口'],['payment','网页收款'],['services','服务接入'],['announcement','首页公告与海报'],['guide','使用教程'],['network','线路监测']];
function settingsForm(name, content) {
  return `<form id="settings-${name}-form" data-settings-form class="panel"><section class="settings-section">${content}<section class="settings-section"><p class="form-error" role="alert"></p><div class="inline-actions"><span class="micro">更改仅在保存后生效。</span><button class="btn btn-primary" type="submit">保存设置 ${icon('check',16)}</button></div></section></form>`;
}
function selectSettingsCategory(category, focus = false) {
  if(!settingsCategories.some(([id])=>id===category))return;
  state.settingsCategory=category;
  document.querySelectorAll('[data-settings-category]').forEach(panel=>{panel.hidden=panel.dataset.settingsCategory!==category;});
  document.querySelectorAll('[data-action="settings-category"]').forEach(button=>{
    const active=button.dataset.category===category;
    button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
    if(active && focus)button.focus();
  });
}
document.addEventListener('keydown',event=>{
  const button=event.target.closest?.('[data-action="settings-category"]');
  if(!button || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  event.preventDefault();
  const ids=settingsCategories.map(([id])=>id),index=ids.indexOf(button.dataset.category);
  const next=event.key==='Home'?0:event.key==='End'?ids.length-1:(index+(event.key==='ArrowRight'?1:-1)+ids.length)%ids.length;
  selectSettingsCategory(ids[next],true);
});
function settingsPage() {
  const settings=state.overview.settings||{};
  const panels={
    site:(window.MediaDockAppearanceEditor?.panel(settings.appearance)||'')+settingsForm('site', `<h2>买家访问与兑换</h2><p class="subtitle">买家领取卡密后，前往用户中心登录、兑换与管理账号。</p><label class="field"><span>用户中心 / 卡密兑换地址</span><input type="url" name="redemption_url" value="${esc(settings.redemption_url||'')}" placeholder="https://user.example.com/"><small>用于网站用户中心入口及闲鱼发卡文案，填写买家可以访问的地址。</small></label><label class="field"><span>闲鱼店铺地址（选填）</span><input type="url" name="xianyu_store_url" value="${esc(settings.xianyu_store_url||'')}" placeholder="https://你的店铺地址"><small>未填写时页面不会显示闲鱼外链，请填写你自己的店铺地址。</small></label><label class="field"><span>发卡网站公开地址</span><input type="url" name="public_base_url" required ${settings.public_base_url_managed?'readonly':''} value="${esc(settings.public_base_url||'')}" placeholder="https://你的发卡网站域名"><small>${settings.public_base_url_managed?'此地址由服务器部署配置管理。':'用于生成买家领取链接。默认地址仅供本机预览，外部买家无法访问 localhost。'}</small></label></section>`),
    payment:settingsForm('payment', `<h2>网页收款码</h2><p class="subtitle">支持微信与支付宝，可稍后设置。保存后买家可在订单页看到收款码，付款仍需人工核对。</p><div class="two-col"><label class="field"><span>微信收款码图片地址</span><input name="payment_wechat_url" value="${esc(settings.payment_wechat_url||'')}" placeholder="https://… 或 /static/…"><small>仅支持 HTTPS 图片或本站静态图片。</small></label><label class="field"><span>支付宝收款码图片地址</span><input name="payment_alipay_url" value="${esc(settings.payment_alipay_url||'')}" placeholder="https://… 或 /static/…"><small>留空表示暂不启用该收款方式。</small></label></div></section>`),
    services:settingsForm('services', `<h2>NextEmby 接口</h2><p class="subtitle">智能体密钥保存在服务端受保护的凭据存储中，页面不会回显已有密钥。</p><label class="field"><span>更新智能体密钥 <span class="micro">（选填）</span></span><input type="password" name="nextemby_token" autocomplete="new-password" placeholder="留空保留现有密钥"><small>使用 NextEmby 管理后台中的智能体接口密钥。</small></label><div class="inline-actions"><span class="setting-state">当前状态：${connectionStatus(state.overview.connections?.nextemby)}</span><button class="btn btn-outline btn-small" data-action="check-nextemby" type="button">${icon('refresh',13)}只读检查连接</button></div></section>`)+`<section class="panel network-settings-panel"><div class="settings-section"><h2>影视库与订阅找片</h2><p class="subtitle">选择允许客户浏览的 Emby 媒体库，并连接 NextFind 搜片。访客想看申请经后台审核后订阅；用户中心客户沿用原有订阅规则。</p><div class="inline-actions"><button type="button" class="btn btn-outline btn-small" data-action="media-settings" data-kind="emby">${icon('film',15)}配置 Emby 片库</button><button type="button" class="btn btn-outline btn-small" data-action="media-settings" data-kind="nextfind">${icon('search',15)}配置 NextFind</button></div></div></section>`,
    announcement:introductionSettingsPanel()+`<section class="panel network-settings-panel"><div class="settings-section"><h2>首页热门海报</h2><p class="subtitle">每天 06:10 更新热门影片资料与图片，图片通过本站加载。未启用时不展示轮播图片。</p><button type="button" class="btn btn-outline btn-small" data-action="featured-settings">${icon('film',15)}配置热门海报</button></div></section>`,
    guide:window.MediaDockGuideEditor?.panel(settings.guide)||'<p>教程编辑器加载失败，请刷新重试。</p>',
    network:`<section class="panel network-settings-panel"><div class="settings-section"><h2>线路监测</h2><p class="subtitle">CDN 可由买家浏览器测速；源站与线路机读取探针在线状态和平均延迟，也可由服务端监测。每分钟更新。</p><div class="callout network-settings-privacy">${icon('shield',17)}<span>浏览器测速地址会公开给买家浏览器，页面仅显示别名；源站、线路机和其他服务端探测地址仍仅管理员可见。</span></div><p class="micro network-settings-help">每个节点可选买家浏览器测速、服务端 HTTP / HTTPS、Ping（ICMP）或妙妙屋 X 探针。分组和项目可自由添加、删除及修改标题；探针真实名称只在下方选择器中显示。各类耗时来源不同，不代表完整播放体验。</p><section class="network-probe-connection"><h3>妙妙屋 X 探针接入</h3><p class="micro">连接后，从节点列表选择要展示的探针。读取在线状态和探针上报的平均延迟，不会修改探针或服务器设置。</p><div id="network-probe-settings"><p class="micro" role="status">正在读取探针连接…</p></div></section><div id="network-monitor-settings"><p class="micro" role="status">正在读取监测设置…</p></div></div></section>`
  };
  return `${pageTitle('设置','按分类管理站点、收款、服务和页面内容。')}<div class="settings-width settings-categories"><div class="settings-category-tabs" role="tablist" aria-label="设置分类">${settingsCategories.map(([id,label])=>`<button type="button" id="settings-tab-${id}" role="tab" aria-controls="settings-panel-${id}" aria-selected="${state.settingsCategory===id}" tabindex="${state.settingsCategory===id?0:-1}" data-action="settings-category" data-category="${id}">${label}</button>`).join('')}</div>${settingsCategories.map(([id])=>`<div role="tabpanel" id="settings-panel-${id}" aria-labelledby="settings-tab-${id}" data-settings-category="${id}" ${state.settingsCategory===id?'':'hidden'}>${panels[id]}</div>`).join('')}<button class="btn btn-outline mt-24" data-action="logout">${icon('logout',15)}退出管理员登录</button></div>`;
}
function purchaseReviewDetail(id) {
  const review=array(state.overview?.purchase_reviews).find(r=>r.request_id===id);if(!review)return toast('审核申请不存在，请刷新订单列表。',true);
  const product=review.product || state.overview.products.find(p=>p.id===review.product_id);
  showModal(`<div class="eyebrow">XIANYU ORDER REVIEW</div><h2 id="modal-title">闲鱼订单审核</h2><p class="subtitle">买家提交的信息需要与闲鱼真实交易核对。</p><div class="key-value"><span>闲鱼订单号</span><strong class="order-id">${esc(review.xianyu_order_id)}</strong></div><div class="key-value"><span>申请号</span><strong class="order-id">${esc(review.request_id)}</strong></div><div class="key-value"><span>套餐 / 标价</span><strong>${esc(product ? productTitle(product) : review.product_id)}${product ? ` / ¥${money(product.listed_price_cents)}` : ''}</strong></div><div class="key-value"><span>买家备注</span><strong>${esc(review.buyer_note||'无')}</strong></div><div class="key-value"><span>审核状态</span>${purchaseReviewBadge(review.status)}</div>${review.review_note ? `<div class="key-value"><span>审核记录</span><strong>${esc(review.review_note)}</strong></div>` : ''}
    ${review.status==='pending_review' ? `<div class="callout mt-24">${icon('info',16)}<span>请在闲鱼核对卖家账号、买家、套餐、数量和实付金额。通过后关联原订单，已有卡密会继续复用。</span></div><form id="purchase-review-approve-form" data-id="${esc(id)}"><label class="field mt-24"><span>已核实的闲鱼买家 ID</span><input name="buyer_id" required maxlength="100" autocomplete="off" placeholder="填写闲鱼订单中的真实买家 ID"><small>填写账号 ID，不是买家自行填写的昵称。</small></label><label class="field"><span>到账核对依据</span><textarea name="evidence_note" required minlength="6" maxlength="240" placeholder="记录核实的套餐、金额和付款状态"></textarea></label><label class="checkbox-label"><input type="checkbox" name="verified" required>已核对真实交易与买家，确认套餐和实付金额相符</label><p class="form-error mt-12" role="alert"></p><button class="btn btn-primary btn-full" type="submit">审核通过，打开原订单 ${icon('arrow',16)}</button><p class="micro mt-12">此操作不会生成卡密。后续在统一发卡流程中交付到原闲鱼会话。</p></form><details class="purchase-review-reject"><summary>信息不符，拒绝申请</summary><form id="purchase-review-reject-form" data-id="${esc(id)}"><label class="field mt-18"><span>内部核对说明</span><textarea name="reason" required minlength="6" maxlength="240" placeholder="记录未通过核对的原因，仅管理员可见"></textarea></label><p class="form-error" role="alert"></p><button class="btn btn-outline btn-full" type="submit">拒绝此申请</button></form></details>` : review.order_id ? `<button class="btn btn-primary btn-full mt-24" data-action="order-detail" data-id="${esc(review.order_id)}">查看关联订单 ${icon('arrow',16)}</button>` : '<p class="micro mt-24">此申请未生成卡密；可在原闲鱼会话中继续核对。</p>'}`, 'order-detail-modal');
}
function newOrderModal(requestedSource) {
  const xhsSource = state.orderSource==='xiaohongshu';
  showModal(`<div class="eyebrow">MANUAL ORDER</div><h2 id="modal-title">录入订单</h2><p class="subtitle">录入实际交易信息。新订单仍需核对到账，才能生成卡密。</p><form id="admin-order-form"><label class="field"><span>购买来源</span><select name="source" required><option value="manual" ${xhsSource?'':'selected'}>手工登记 / 未指定渠道</option><option value="web">网页购买</option><option value="xianyu">闲鱼购买</option><option value="xiaohongshu" ${xhsSource?'selected':''}>小红书购买</option></select><small>按实际购买渠道归组，保存后仍需核对到账。</small></label><label class="field"><span>外部订单号</span><input name="order_id" required maxlength="100" placeholder="填写实际交易订单号" autocomplete="off"><small>使用真实、唯一的订单号，避免同一交易重复录入。</small></label><label class="field"><span>购买套餐</span><select name="product_id" required><option value="">请选择对应规格</option>${sortedProducts(state.overview.products).map(p=>`<option value="${esc(p.id)}">${esc(productTitle(p))} · ¥${money(p.listed_price_cents)} · ${p.duration_days} 天</option>`).join('')}</select></label><label class="field"><span>交付方式</span><select name="delivery_mode"><option value="direct">直接提供卡密</option><option value="claim">提供网页领取链接</option></select></label><label class="field"><span>买家备注 <span class="micro">（可选）</span></span><input name="buyer_note" maxlength="120" placeholder="昵称、交易说明等，不要填写账号密码"></label><p class="form-error" id="admin-order-error" role="alert"></p><button class="btn btn-primary btn-full" type="submit">保存待核对订单</button></form>`);
  const source = requestedSource || (state.tab==='delivery'?state.deliverySource:state.orderSource);
  const form = document.getElementById('admin-order-form');
  form.elements.source.value = ['web','xianyu','xiaohongshu','manual'].includes(source)?source:'manual';
  updateManualOrderHint(form);
}
function updateManualOrderHint(form) {
  const isXianyu = form.elements.source.value==='xianyu';
  form.elements.order_id.closest('.field').querySelector('small').textContent = isXianyu
    ? '填写闲鱼原始交易号；系统按已绑定卖家去重，旧单冲突请打开原订单核查。'
    : '使用真实、唯一的订单号，避免同一交易重复录入。';
}
function orderDetail(id) {
  const order=state.overview.orders.find(o=>oid(o)===id); if(!order) return toast('订单未找到，请刷新列表。',true);
  const product=orderProduct(order), status=order.status==='processing'?'issuing':order.status, canIssue=['approved','eligible'].includes(status), hasCard=['issued','ready','delivered','claimed'].includes(status);
  showModal(`<div class="eyebrow">ORDER DETAILS</div><h2 id="modal-title">订单详情</h2><p class="subtitle">${esc(product ? productTitle(product) : order.product_id)}</p><div class="key-value"><span>订单号</span><strong class="order-id">${esc(id)}</strong></div><div class="key-value"><span>状态</span>${badge(status)}</div><div class="key-value"><span>购买来源</span><strong>${esc(orderSourceLabel(order))}</strong></div><div class="key-value"><span>套餐规格</span><strong>${esc(product?.template_name||'—')} · ${esc(product?.duration_days||'—')} 天 · 1 张</strong></div><div class="key-value"><span>标价 / 交付方式</span><strong>${product ? `¥${money(product.listed_price_cents)}`:'—'} / ${order.delivery_mode==='claim'?'网页领取':'直接发卡'}</strong></div><div class="key-value"><span>买家备注</span><strong>${esc(order.buyer_note||order.contact_note||'无')}</strong></div>${order.payment_note?`<div class="key-value"><span>买家付款说明</span><strong>${esc(order.payment_note)}</strong></div>`:''}${order.evidence_note?`<div class="key-value"><span>到账核对依据</span><strong>${esc(order.evidence_note)}</strong></div>`:''}${status==='needs_review'||status==='failed'?`<div class="callout danger mt-24">${icon('info',16)}<span>此订单处理结果需要人工核查。请核对 NextEmby 卡密批次和闲鱼聊天记录，暂不可再次生成卡密。${(order.error_message||order.last_error)?`<br>${esc(order.error_message||order.last_error)}`:''}</span></div>`:''}${['pending_payment','pending_review','payment_reported','awaiting_review'].includes(status)?`<div class="order-detail-actions"><h3>核对实际到账</h3><p class="micro">以真实支付记录为准；买家聊天或自报付款不能作为到账凭据。</p><form id="approve-form" data-id="${esc(id)}"><label class="field"><span>到账核对依据</span><textarea name="evidence_note" required minlength="6" maxlength="240" placeholder="记录支付渠道、到账金额及交易凭据；无需填写完整敏感信息"></textarea></label><p class="form-error" id="approve-error" role="alert"></p><button class="btn btn-secondary btn-full" type="submit">确认已核对到账</button></form></div>`:''}${canIssue?`<div class="order-detail-actions"><h3>生成对应兑换卡</h3><p class="micro mb-20">将实际调用 NextEmby，生成此套餐的 1 张卡。生成后按此订单的交付方式提供卡密或领取链接。</p><button class="btn btn-primary btn-full" data-action="issue" data-id="${esc(id)}">${icon('key',16)}生成 1 张卡</button><p class="form-error" id="issue-error" role="alert"></p></div>`:''}${status==='issuing'?'<div class="callout mt-24">正在生成卡密，请等待并刷新状态，不要重复提交。</div>':''}${hasCard?`<div class="order-detail-actions"><h3>卡密与交付</h3><p class="micro mb-20">查看或复制已有卡密，不会再次生成。复制内容不会自动标记消息已发送。</p><div class="inline-actions"><button class="btn btn-primary" data-action="delivery" data-id="${esc(id)}">${icon('copy',15)}查看发货内容</button>${order.delivery_mode==='claim'?`<button class="btn btn-secondary" data-action="make-claim" data-id="${esc(id)}">${icon('link',15)}获取领取链接</button>`:''}</div><div id="delivery-content"></div></div>`:''}<div class="inline-actions mt-24"><button class="btn btn-outline btn-small" data-action="refresh-order-detail" data-id="${esc(id)}">${icon('refresh',14)}刷新订单状态</button></div>`,'order-detail-modal');
}
async function refreshAfterAction(id) { await refreshOverview(); renderAdminTab(); if(id) orderDetail(id); }
function mediaLibraryOptions(libraries,selected) {
  const choices=new Map(array(libraries).filter(item=>item && typeof item.id==='string').map(item=>[item.id,{id:item.id,name:String(item.name||'未命名媒体库')} ]));
  for(const id of selected)if(!choices.has(id))choices.set(id,{id,name:'已选媒体库（待读取名称）'});
  return [...choices.values()].map(item=>`<label class="media-library-option"><input type="checkbox" name="library_id" value="${esc(item.id)}" ${selected.has(item.id)?'checked':''}><span>${esc(item.name)}</span></label>`).join('');
}
function updateMediaLibraryCount(form) {
  const selected=[...form.querySelectorAll('[name="library_id"]:checked')].map(input=>input.value),available=new Set(selected);
  const order=array(form.embyLibraryOrder).filter(id=>available.has(id));
  for(const id of selected)if(!order.includes(id))order.push(id);
  form.embyLibraryOrder=order;
  const count=form.querySelector('#emby-library-count');if(count)count.textContent=`已选 ${order.length} 个`;
  const target=form.querySelector('#emby-library-order');if(!target)return;
  const names=new Map([...form.querySelectorAll('.media-library-option')].map(label=>[label.querySelector('input').value,label.querySelector('span').textContent]));
  target.innerHTML=order.map((id,index)=>`<li class="media-library-order-row"><span class="media-library-order-number" aria-hidden="true">${String(index+1).padStart(2,'0')}</span><span class="media-library-order-name">${esc(names.get(id)||'已选媒体库')}</span><div class="media-library-order-buttons"><button type="button" class="media-library-move" data-action="emby-library-move" data-library-id="${esc(id)}" data-direction="up" aria-label="上移 ${esc(names.get(id)||'媒体库')}" ${index===0?'disabled':''}>↑</button><button type="button" class="media-library-move" data-action="emby-library-move" data-library-id="${esc(id)}" data-direction="down" aria-label="下移 ${esc(names.get(id)||'媒体库')}" ${index===order.length-1?'disabled':''}>↓</button></div></li>`).join('')||'<li class="micro media-library-order-empty">勾选媒体库后，可在这里调整展示顺序。</li>';
}
function moveEmbyLibrary(button) {
  const form=button.closest('form');if(!form || button.disabled)return;
  updateMediaLibraryCount(form);
  const order=form.embyLibraryOrder,index=order.indexOf(button.dataset.libraryId),next=index+(button.dataset.direction==='up'?-1:1);
  if(index<0 || next<0 || next>=order.length)return;
  [order[index],order[next]]=[order[next],order[index]];updateMediaLibraryCount(form);
  const buttons=[...form.querySelectorAll('[data-action="emby-library-move"]')].filter(item=>item.dataset.libraryId===button.dataset.libraryId);
  (buttons.find(item=>item.dataset.direction===button.dataset.direction&&!item.disabled)||buttons.find(item=>!item.disabled))?.focus({preventScroll:true});
}
function mediaIntegrationModal(kind,result,libraries=[]) {
  const emby=kind==='emby',ids=array(result.library_ids).filter(id=>typeof id==='string'),hasToken=Boolean(result.token_configured);
  showModal(`<div class="eyebrow">MEDIA / CONNECTION</div><h2 id="modal-title">${emby?'Emby 影视库':'NextFind 订阅找片'}</h2><p class="subtitle">${emby?'选择允许客户浏览的媒体库。连接地址和密钥仅保存在服务器。':'展示搜片结果与现有订阅进度。客户无法添加、取消订阅或执行转存。'}</p>
    <form id="media-integration-form" data-kind="${kind}" data-saved-base="${esc(result.base_url||'')}"><div class="media-integration-body"><div class="media-connection-grid">
    <label class="field"><span>服务地址</span><input type="url" name="base_url" required value="${esc(result.base_url||'')}" placeholder="${emby?'https://emby.example.com':'https://nextfind.example.com'}"></label>
    <label class="field"><span>${emby?'Emby API 密钥':'NextFind 智能体 API Key'}</span><input type="password" name="token" autocomplete="new-password" placeholder="${hasToken?'已配置；留空保留':'填写服务的 API 密钥'}"><small>已有密钥不会回显，留空即可保留。</small></label></div>
    ${emby?`<section class="media-library-section" aria-labelledby="emby-library-title"><div class="media-library-heading"><h3 id="emby-library-title">允许公开展示的媒体库</h3><span id="emby-library-count" role="status">已选 ${ids.length} 个</span></div><p class="micro">首页展示所选媒体库与封面，点入后展示该库影片海报。最多选择 128 个，保存后生效。</p><div id="emby-library-options" class="media-library-options">${mediaLibraryOptions(libraries,new Set(ids))||'<p class="media-library-empty micro">先保存服务地址和密钥，再读取媒体库进行选择。</p>'}</div><button type="button" class="btn btn-outline btn-small" data-action="emby-libraries" ${!hasToken?'disabled':''}>读取可选媒体库</button><div class="media-library-order-heading"><h3>展示顺序</h3><span class="micro">用上下箭头调整，保存后生效</span></div><ol id="emby-library-order" class="media-library-order" aria-label="媒体库展示顺序"></ol></section>`:''}
    <div class="media-visibility-options"><label class="checkbox-line"><input type="checkbox" name="enabled" ${result.enabled?'checked':''}><span>向客户开放${emby?'所选媒体库':'搜片结果与追更进度'}</span></label><label class="checkbox-line media-clear-token"><input type="checkbox" name="clear_token"><span>清除已有密钥并关闭展示</span></label></div></div>
    <div class="media-integration-actions"><p class="form-error" role="alert"></p><div class="media-save-row"><span class="micro">更改仅在保存后生效</span><button class="btn btn-primary" type="submit">保存配置 ${icon('check',16)}</button></div></div></form>`, 'media-integration-modal');
  if(emby){const form=document.getElementById('media-integration-form');form.embyLibraryOrder=ids;updateMediaLibraryCount(form);}
}
async function loadEmbyLibraries(button) {
  const form=button.closest('form'),target=form.querySelector('#emby-library-options');
  if(form.elements.base_url.value.trim()!==form.dataset.savedBase || form.elements.token.value.trim() || form.elements.clear_token.checked)throw new Error('连接信息有未保存的更改，请先保存配置，再读取媒体库。');
  const result=await api('/api/admin/emby-library/libraries',{timeout:20000});
  if(!target.isConnected)return;
  const selected=new Set([...form.querySelectorAll('[name="library_id"]:checked')].map(input=>input.value));
  target.innerHTML=mediaLibraryOptions(array(result.libraries),selected)||'<p class="media-library-empty micro">没有可读取的媒体库，请检查 Emby 凭据与媒体库配置。</p>';
  updateMediaLibraryCount(form);
}
document.addEventListener('change',event=>{
  const form=event.target.closest?.('#media-integration-form');if(!form)return;
  if(event.target.name==='library_id')updateMediaLibraryCount(form);
  if(event.target.name==='clear_token'){form.elements.enabled.disabled=event.target.checked;if(event.target.checked)form.elements.enabled.checked=false;}
});
function featuredSettingsModal(result) {
  showModal(`<div class="eyebrow">HOME / FEATURED MOVIES</div><h2 id="modal-title">首页热门海报</h2><p class="subtitle">使用 TMDB 本周热门电影榜单，优先读取中文片名，最多展示 6 部。更新失败时保留上次内容。</p><form id="featured-movies-form"><label class="field"><span>自动更新</span><select name="enabled"><option value="false" ${!result.enabled?'selected':''}>关闭 · 暂停展示热门影片</option><option value="true" ${result.enabled?'selected':''}>开启 · 每天 06:10 更新（北京时间）</option></select></label><label class="field"><span>API Read Access Token</span><input type="password" name="token" autocomplete="new-password" placeholder="${result.token_configured?'已配置；留空保留原令牌':'粘贴 TMDB API Read Access Token'}"><small>仅保存在服务器凭据存储，不向买家公开。<a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noopener noreferrer">前往 TMDB 获取 ${icon('external',12)}</a></small></label><label class="checkbox-line"><input type="checkbox" name="clear_token">清除已有令牌并关闭更新</label><div class="callout mt-18">${icon('clock',15)}<span>${result.last_success?`上次成功更新：${esc(new Date(result.last_success).toLocaleString('zh-CN'))} · ${Number(result.movie_count)||0} 部影片`:'尚未获取热门影片。填写令牌、启用并保存后，点击“立即更新海报”验证连接。'}</span></div><p class="form-error" role="alert"></p><div class="inline-actions"><button class="btn btn-primary" type="submit">保存配置</button><button class="btn btn-outline" type="button" data-action="featured-refresh" ${!result.enabled || !result.token_configured?'disabled':''}>立即更新海报</button></div><p class="micro mt-18">热门榜单用于影片资讯展示，不代表影视服片库已收录。使用范围以 TMDB 的授权条款为准。</p></form>`);
}
document.addEventListener('click', async event => {
  const button=event.target.closest('[data-action]'); if(!button) return;
  const action=button.dataset.action;
  if(action==='user-center-unconfigured') return toast('用户中心暂未配置，请联系站点管理员获取入口。');
  if(action==='close-modal') return closeModal();
  if(action==='show-introduction') return openPublicIntroduction();
  if(action==='intro-preview') {const form=button.closest('form');if(form.reportValidity())showIntroduction(false,introductionFormValue(form),true);return;}
  if(action==='intro-add-section') {const rows=button.closest('form').querySelector('[data-intro-sections]');if(rows.children.length>=12)return toast('最多添加 12 个段落。',true);rows.insertAdjacentHTML('beforeend',introductionSectionFields());rows.lastElementChild.querySelector('input').focus();return;}
  if(action==='intro-remove-section') {const row=button.closest('.intro-editor-section');if(row.parentElement.children.length<=1)return toast('请至少保留一个公告段落。',true);row.remove();return;}
  if(action==='emby-library-move') return moveEmbyLibrary(button);
  if(action==='reload') return location.reload();
  if(action==='network-refresh') {if(Date.now()-(shopNetworkMonitor.lastManual||0)<5000)return;shopNetworkMonitor.lastManual=Date.now();startShopNetworkMonitor();return;}
  if(action==='copy') return copy(button.dataset.copy||'');
  if(action==='purchase') return purchaseModal(button.dataset.product);
  if(action==='purchase-mode') {const modal=modalRoot.querySelector('.purchase-modal'),form=modal?.querySelector('form'),draft={...(modal?.purchaseDraft||{}),...(form?Object.fromEntries(new FormData(form)):{})},returnFocus=state.modalReturnFocus;purchaseModal(button.dataset.product,button.dataset.mode,draft);state.modalReturnFocus=returnFocus;modalRoot.querySelector(`[data-action="purchase-mode"][data-mode="${button.dataset.mode}"]`)?.focus();return;}
  if(action==='purchase-review-detail') return purchaseReviewDetail(button.dataset.id);
  if(action==='refresh-purchase-review') {const release=busy(button);try{await renderPurchaseReview(button.dataset.id);}finally{release();}return;}
  if(action==='filter') { state.filter=button.dataset.filter; document.querySelectorAll('[data-action="filter"]').forEach(el=>{el.classList.toggle('active',el===button);el.setAttribute('aria-pressed',String(el===button));});const products=document.getElementById('products');products.classList.toggle('filtered',state.filter!=='all');products.innerHTML=productCards();return; }
  if(action==='settings-category') return selectSettingsCategory(button.dataset.category);
  if(action==='admin-tab') { if(button.dataset.tab==='orders')return selectOrderSource('all');if(button.dataset.tab==='xianyu'){state.deliveryView='settings';return selectDelivery('delivery-channel','xianyu');}state.tab=button.dataset.tab;renderAdminTab();return; }
  if(['delivery-view','delivery-channel','delivery-stage'].includes(action)) return selectDelivery(action,button.dataset.view||button.dataset.source||button.dataset.stage);
  if(action==='order-source') return selectOrderSource(button.dataset.source);
  if(action==='toggle-order-groups') {state.orderGroupsExpanded=!state.orderGroupsExpanded;updateOrderNavigation();return;}
  if(['network-group-add','network-node-add','network-group-remove','network-node-remove','network-monitor-undo'].includes(action)) return networkMonitorEditorAction(action,button);
  if(action==='network-monitor-reload') return loadNetworkMonitorSettings();
  if(action==='network-probe-reload') return loadNetworkProbeSettings();
  if(action==='network-probe-refresh') return refreshNetworkProbe(button);
  if(action==='new-order') return newOrderModal(button.dataset.source);
  if(action==='order-detail') return orderDetail(button.dataset.id);
  const release=busy(button);
  try {
    if(action==='media-settings') {const kind=button.dataset.kind==='emby'?'emby':'nextfind',result=await api(`/api/admin/${kind==='emby'?'emby-library':'nextfind-catalog'}`);if(button.isConnected)mediaIntegrationModal(kind,result);}
    else if(action==='emby-libraries') await loadEmbyLibraries(button);
    else if(action==='featured-settings') {const result=await api('/api/admin/featured-movies');if(button.isConnected)featuredSettingsModal(result);}
    else if(action==='featured-refresh') {const result=await api('/api/admin/featured-movies/refresh',{method:'POST',body:{},timeout:90000});if(button.isConnected)featuredSettingsModal(result);toast('热门海报已更新');}
    else if(action==='logout') {await api('/api/auth/logout',{method:'POST',body:{}});state.bridgeConfig=null;closeModal();await renderAdmin();}
    else if(action==='refresh-admin') {await refreshOverview();renderAdminShell();toast('状态已刷新');}
    else if(action==='refresh-assistant-config') {await refreshOverview();renderAdminTab();}
    else if(action==='refresh-order-detail') await refreshAfterAction(button.dataset.id);
    else if(action==='refresh-public-order') await renderPublicOrder(button.dataset.id);
    else if(action==='check-nextemby') {const result=await api('/api/admin/nextemby/check',{method:'POST',body:{}});await refreshOverview();if(state.tab==='settings'){const status=document.querySelector('#settings-services-form .setting-state');if(status)status.innerHTML='当前状态：'+connectionStatus(state.overview.connections?.nextemby);}else renderAdminTab();toast(result.message||'只读连接检查已完成');}
    else if(action==='issue') {
      if(!window.confirm('将实际调用 NextEmby，为此订单生成 1 张卡密。请确认订单套餐和到账核对无误。继续生成？')) return;
      try {await api(`/api/admin/orders/${encodeURIComponent(button.dataset.id)}/issue`,{method:'POST',body:{}});await refreshAfterAction(button.dataset.id);toast('卡密生成完成，请查看发货内容。');}
      catch(error) {await refreshAfterAction(button.dataset.id).catch(()=>{});toast(`${errorText(error)} 请先刷新并核查结果，勿重复生成。`,true);}
    } else if(action==='bridge-config') {
      const result=await api('/api/admin/bridge-config');
      state.bridgeConfig=result;
      const presets=array(result.presets), products=[...new Map(presets.map(p=>[p.product_id,p])).values()];
      document.getElementById('bridge-config-content').innerHTML=`<label class="field mt-18"><span>发卡接口地址</span><input readonly value="${esc(result.url)}"></label><label class="field"><span>连接器密钥</span><input type="password" readonly value="${esc(result.token)}"><small>仅填入同一部署主机上闲鱼连接器的认证配置，不要发送给买家。</small></label><button class="btn btn-outline btn-small" data-action="copy" data-copy="${esc(result.token)}">${icon('copy',13)}复制连接器密钥</button>${presets.length?`<div class="order-detail-actions"><h3>复制套餐 API 配置</h3><p class="micro mb-20">选择套餐与交付方式，复制完整配置 JSON 后，粘贴到闲鱼工作台的「卡券管理 → API 配置」。配置包含连接器凭证，请勿发送给买家。</p><div class="two-col"><label class="field"><span>对应套餐</span><select id="bridge-preset-product">${products.map(p=>`<option value="${esc(p.product_id)}">${esc(p.name||p.product_id)}</option>`).join('')}</select></label><label class="field"><span>交付方式</span><select id="bridge-preset-mode"><option value="direct">闲鱼直接发送卡密</option><option value="claim">发送网页领取链接</option></select></label></div><button class="btn btn-primary btn-small" data-action="copy-bridge-config">${icon('copy',13)}复制完整 API 配置 JSON</button><div class="callout mt-18">${icon('info',16)}<span>工作台的「测试」按钮不会取卡或生成卡密。完成商品、规格和发货规则绑定后，由已核验的付款事件触发发卡。<br>网页领取模式需要配置买家可访问的网站公开地址。</span></div></div>`:'<p class="micro mt-18">当前服务未返回套餐预设，请检查服务版本后重新读取配置。</p>'}`;
    } else if(action==='copy-bridge-config') {
      const productId=document.getElementById('bridge-preset-product')?.value, mode=document.getElementById('bridge-preset-mode')?.value;
      const preset=array(state.bridgeConfig?.presets).find(p=>p.product_id===productId && p.delivery_mode===mode);
      if(!preset?.config || typeof preset.config!=='object') throw new Error('此套餐的配置暂不可用，请重新读取连接器配置。');
      await copy(JSON.stringify(preset.config,null,2),'完整 API 配置已复制，请粘贴到闲鱼卡券管理。');
    } else if(action==='delivery') {
      const result=await api(`/api/admin/orders/${encodeURIComponent(button.dataset.id)}/delivery`);
      const message=result.message||result.card||'';
      document.getElementById('delivery-content').innerHTML=`<label class="field mt-24"><span>发给买家的内容</span><textarea readonly class="delivery-message">${esc(message)}</textarea></label><button class="btn btn-outline btn-small" data-action="copy" data-copy="${esc(message)}">${icon('copy',13)}复制发货内容</button><p class="micro mt-12">复制后请发给此订单对应买家，并确认发送结果。</p>`;
    } else if(action==='make-claim') {
      const result=await api(`/api/admin/orders/${encodeURIComponent(button.dataset.id)}/claim`,{method:'POST',body:{}}), url=safeUrl(result.claim_url);
      if(!url) throw new Error('服务未返回有效领取链接，请检查公开地址设置。');
      const isLocal=['localhost','127.0.0.1','::1','[::1]'].includes(new URL(url).hostname);
      document.getElementById('delivery-content').innerHTML=`<label class="field mt-24"><span>买家专属领取链接</span><textarea readonly>${esc(url)}</textarea></label><button class="btn btn-outline btn-small" data-action="copy" data-copy="${esc(url)}">${icon('copy',13)}复制领取链接</button>${isLocal?'<div class="callout mt-18">此链接使用本机地址，外部买家无法访问。请先配置网站公开地址。</div>':'<p class="micro mt-12">请仅发送给对应买家，持有链接即可领取此订单卡密。</p>'}`;
    }
  } catch(error) {toast(errorText(error),true);if(error.status===401 && ['/admin','/admin/'].includes(location.pathname)) renderAdmin();}
  finally {release();}
});
document.addEventListener('input',event=>{if(event.target.id==='order-search'){state.search=event.target.value;document.getElementById('order-table-content').innerHTML=orderResults();}});
document.addEventListener('change',event=>{if(event.target.form?.id!=='network-monitor-form')return;if(event.target.matches('[data-network-method]'))changeNetworkMonitorMethod(event.target);else if(event.target.matches('[data-network-move]'))moveNetworkMonitorNode(event.target);});
document.addEventListener('input',event=>{if(event.target.form?.id==='network-monitor-form'){networkMonitorEditor.dirty=true;const feedback=event.target.form.querySelector('.network-settings-feedback');if(feedback)feedback.textContent='有未保存的更改。';}});
document.addEventListener('change',event=>{if(event.target.id==='order-status-filter'){state.status=event.target.value;document.getElementById('order-table-content').innerHTML=orderResults();}});
document.addEventListener('submit',async event=>{
  const form=event.target;if(form.matches?.('[data-media-form]')){event.preventDefault();return window.MediaDockMedia?.submit(form);}if(!form.id)return;event.preventDefault();
  if(form.id==='purchase-form' && state.catalog?.payment_configured!==true){
    form.querySelector('[type="submit"]').disabled=true;
    form.querySelector('.form-error').textContent='网页收款尚未配置，现在仅登记待付款订单。可以通过其它入口完成购买。';
    return;
  }
  if(form.id==='network-monitor-form') return saveNetworkMonitorSettings(form);
  if(form.id==='network-probe-form') return saveNetworkProbeSettings(form);
  const values=Object.fromEntries(new FormData(form)), button=form.querySelector('[type="submit"]'), release=busy(button), errorEl=form.querySelector('.form-error');
  if(errorEl)errorEl.textContent='';
  try {
    if(form.id==='auth-form') {if(form.dataset.mode==='setup' && values.password!==values.confirmation)throw new Error('两次输入的密码不一致。');await api(`/api/auth/${form.dataset.mode}`,{method:'POST',body:{username:values.username,password:values.password}});form.reset();await renderAdmin();}
    else if(form.id==='purchase-form') {const result=await api('/api/public/orders',{method:'POST',body:{product_id:form.dataset.product,contact_note:values.contact_note}});if(!result.order_id)throw new Error('订单返回不完整，请联系管理员核对。');if(result.access_token)storePublicToken(result.order_id,result.access_token);closeModal();history.pushState({},'',`/?order=${encodeURIComponent(result.order_id)}`);await renderPublicOrder(result.order_id,{...result,product_id:form.dataset.product});}
    else if(form.id==='xianyu-purchase-form') {const result=await api('/api/public/purchase-reviews',{method:'POST',body:{product_id:form.dataset.product,xianyu_order_id:values.xianyu_order_id.trim(),buyer_note:values.buyer_note.trim()}});if(!result.request_id || !result.access_token)throw new Error('审核申请返回不完整，请在闲鱼联系卖家核对。');storePublicToken(result.request_id,result.access_token);closeModal();history.pushState({},'',`/?review=${encodeURIComponent(result.request_id)}`);await renderPurchaseReview(result.request_id,{...result,product_id:form.dataset.product});}
    else if(form.id==='purchase-review-access-form'){storePublicToken(form.dataset.id,values.token.trim());await renderPurchaseReview(form.dataset.id);}
    else if(form.id==='purchase-review-approve-form'){if(values.verified!=='on')throw new Error('请先核对闲鱼真实交易并勾选确认。');const result=await api(`/api/admin/purchase-reviews/${encodeURIComponent(form.dataset.id)}/approve`,{method:'POST',body:{buyer_id:values.buyer_id.trim(),evidence_note:values.evidence_note.replace(/\s+/g,' ')}});await refreshAfterAction(result.order_id);toast('审核已通过，已打开关联的闲鱼订单。');}
    else if(form.id==='purchase-review-reject-form'){const id=form.dataset.id;await api(`/api/admin/purchase-reviews/${encodeURIComponent(id)}/reject`,{method:'POST',body:{reason:values.reason.replace(/\s+/g,' ')}});await refreshOverview();renderAdminTab();purchaseReviewDetail(id);toast('审核结果已记录。');}
    else if(form.id==='order-access-form'){storePublicToken(form.dataset.id,values.token.trim());await renderPublicOrder(form.dataset.id);}
    else if(form.id==='paid-notice-form'){await api(`/api/public/orders/${encodeURIComponent(form.dataset.id)}/paid-notice`,{method:'POST',headers:{'X-Order-Token':publicToken(form.dataset.id)},body:{payment_note:values.payment_note.replace(/\s+/g,' ')}});await renderPublicOrder(form.dataset.id);toast('付款说明已提交，等待管理员核对到账。');}
    else if(form.id==='claim-form'){const token=parseClaimInput(values.token);if(!token)throw new Error('请输入有效的领取凭证。');const result=await api('/api/claim',{method:'POST',body:{token}});if(!result.card)throw new Error('卡密尚未准备好，请等待订单处理完成。');claimSuccess(result);}
    else if(form.id==='admin-order-form'){const result=await api('/api/admin/orders',{method:'POST',body:values});closeModal();await refreshAfterAction(result.order_id||result.id||values.order_id);toast('订单已打开。');}
    else if(form.id==='approve-form'){await api(`/api/admin/orders/${encodeURIComponent(form.dataset.id)}/approve`,{method:'POST',body:{evidence_note:values.evidence_note.replace(/\s+/g,' ')}});await refreshAfterAction(form.dataset.id);toast('到账核对已记录，可以生成卡密。');}
    else if(form.id==='xianyu-account-form'){await api('/api/admin/settings',{method:'POST',body:{xianyu_account_id:values.xianyu_account_id}});await refreshOverview();renderAdminTab();toast('闲鱼账号绑定已保存。');}
    else if(form.id.startsWith('binding-form-')){await api(`/api/admin/products/${encodeURIComponent(form.dataset.product)}`,{method:'POST',body:{xianyu_item_id:values.xianyu_item_id,xianyu_sku_id:values.xianyu_sku_id,enabled:values.enabled==='on'}});await refreshOverview();renderAdminTab();toast('规格绑定已保存。');}
    else if(form.id==='media-integration-form'){
      const emby=form.dataset.kind==='emby',body={base_url:values.base_url,enabled:values.enabled==='on',token:values.token||'',clear_token:values.clear_token==='on'};if(emby){updateMediaLibraryCount(form);body.library_ids=[...form.embyLibraryOrder];}
      const libraries=[...form.querySelectorAll('.media-library-option')].map(label=>({id:label.querySelector('input').value,name:label.querySelector('span').textContent}));
      try{const result=await api(`/api/admin/${emby?'emby-library':'nextfind-catalog'}`,{method:'POST',body});if(form.isConnected)mediaIntegrationModal(emby?'emby':'nextfind',result,libraries);toast('媒体连接配置已保存');}finally{form.elements.token.value='';}
    }
    else if(form.id==='featured-movies-form'){
      try {const result=await api('/api/admin/featured-movies',{method:'POST',body:{enabled:values.enabled==='true',token:values.token||'',clear_token:values.clear_token==='on'}});if(form.isConnected)featuredSettingsModal(result);toast('热门海报配置已保存');}finally{form.elements.token.value='';}
    }
    else if(form.id==='appearance-form'){
      const result=await api('/api/admin/settings',{method:'POST',body:{appearance:window.MediaDockAppearanceEditor.value(form)}});
      state.overview.settings=result.settings;applyAppearance(result.settings.appearance);
      toast('页面装修已保存，前台刷新后即可看到。');
    }
    else if(form.id==='guide-form'){
      const result=await api('/api/admin/settings',{method:'POST',body:{guide:window.MediaDockGuideEditor.value(form)}});
      state.overview.settings=result.settings;if(state.catalog)state.catalog.guide=result.settings.guide;toast('使用教程已保存，前台刷新后即可看到。');
    }
    else if(form.id==='introduction-form'){
      const result=await api('/api/admin/settings',{method:'POST',body:{introduction:introductionFormValue(form)}});
      state.overview.settings=result.settings;
      if(state.catalog)state.catalog.introduction=result.settings.introduction;
      toast('首页公告已保存。');
    }
    else if(form.matches('[data-settings-form]')){
      if(!values.nextemby_token)delete values.nextemby_token;
      for(const field of ['payment_wechat_url','payment_alipay_url'])if(values[field] && !(values[field].startsWith('/static/') || /^https:\/\//i.test(values[field])))throw new Error('收款码图片仅支持 HTTPS 地址或 /static/ 开头的本站路径。');
      const result=await api('/api/admin/settings',{method:'POST',body:values});state.overview.settings=result.settings;if(form.elements.nextemby_token)form.elements.nextemby_token.value='';toast('设置已保存。');
    }
  }catch(error){if(errorEl?.isConnected)errorEl.textContent=errorText(error);else toast(errorText(error),true);}
  finally{release();}
});
let assistantLogoutStarted = false;
async function logoutFromAssistant() {
  if(assistantLogoutStarted) return;
  assistantLogoutStarted = true;
  app.innerHTML = '<div class="loading" role="status">正在退出管理后台…</div>';
  try {
    await api('/api/auth/logout', {method:'POST', body:{}});
    clearTimeout(assistantLoadTimer);
    state.bridgeConfig = null;
    closeModal();
    const url = new URL(location.href);
    url.searchParams.delete('logout');
    history.replaceState({}, '', url.pathname + url.search + url.hash);
    await renderAdmin();
  } catch(error) {
    app.innerHTML = `<div class="container"><main class="page-center">${empty(errorText(error),'退出尚未完成','<button class="btn btn-secondary" data-action="reload">重新尝试</button>')}</main></div>`;
  }
}
window.addEventListener('popstate',()=>{closeModal();start();});
window.addEventListener('hashchange',()=>{if(location.pathname==='/claim')renderClaim();else if(location.pathname==='/' && location.hash==='#network')location.replace('/status');else if(location.pathname==='/' && location.hash==='#plans' && !new URLSearchParams(location.search).get('order'))start();});
async function start(){await loadAppearance();window.MediaDockSubscriptions?.stopAdmin();publicOrderGeneration++;if(location.pathname==='/' && location.hash==='#plans' && !new URLSearchParams(location.search).get('order'))history.replaceState(null,'','/plans');window.MediaDockMedia?.stop();window.MediaDockCarousel?.stop();if(location.pathname==='/plans' || location.pathname==='/plans/')return renderPlans();if(location.pathname==='/library' || location.pathname==='/library/')return renderMediaPage('library');if(location.pathname==='/discover' || location.pathname==='/discover/')return renderMediaPage('discovery');if(location.pathname==='/' && location.hash==='#network')return location.replace('/status');if(location.pathname==='/status' || location.pathname==='/status/')return renderServiceStatus();if(location.pathname==='/guide' || location.pathname==='/guide/')return renderGuide();if(['/admin','/admin/'].includes(location.pathname)){if(new URLSearchParams(location.search).get('logout')==='1')return logoutFromAssistant();return renderAdmin();}if(location.pathname.startsWith('/claim'))return renderClaim();return renderShop();}
start();
