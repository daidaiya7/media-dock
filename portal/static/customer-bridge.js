(function () {
  'use strict';
  const PORTAL = location.origin;
  let CENTER = '';
  const CHANNEL = 'mediadock-customer-v1';
  const pending = new Map();
  let frame = null, nonce = '', boot = null, readyResolve = null, readyReject = null, bootTimer = null, counter = 0, epoch = 0;
  const unavailable = () => ({connected:false, reason:'unavailable', daily_limit:null, daily_used:null});
  const numeric = value => Number.isInteger(value) && value >= 0 && value <= 1000000;
  const uncertain = () => ({status:'uncertain', message:'提交结果待核查，请先到用户中心查看记录，勿重复提交。'});

  function reset() {
    epoch += 1;
    clearTimeout(bootTimer); bootTimer = null;
    if (readyReject) readyReject(new Error('unavailable'));
    if (frame) frame.remove();
    frame = null; boot = null; readyResolve = null; readyReject = null;
    for (const record of pending.values()) { clearTimeout(record.timer); record.resolve(record.method === 'subscribe' ? uncertain() : unavailable()); }
    pending.clear();
  }

  function ensureFrame() {
    if (boot) return boot;
    if (location.origin !== PORTAL || !CENTER || !window.crypto?.getRandomValues) return Promise.reject(new Error('unavailable'));
    nonce = Array.from(crypto.getRandomValues(new Uint8Array(32)), byte => byte.toString(16).padStart(2,'0')).join('');
    boot = new Promise((resolve,reject) => {
      readyReject = reject;
      bootTimer = setTimeout(() => { reset(); }, 15000);
      readyResolve = () => { clearTimeout(bootTimer); bootTimer = null; readyResolve = null; readyReject = null; resolve(); };
      frame = document.createElement('iframe');
      frame.title = '影视服用户中心授权连接';
      frame.hidden = true;
      frame.setAttribute('aria-hidden','true');
      frame.setAttribute('referrerpolicy','no-referrer');
      frame.src = `${CENTER}/mediadock-connect/index.html#${nonce}`;
      document.body.append(frame);
    });
    return boot;
  }

  function safeStatus(raw) {
    if (raw?.connected === true && numeric(raw.daily_limit) && numeric(raw.daily_used)) {
      return {connected:true,daily_limit:raw.daily_limit,daily_used:raw.daily_used,remaining:Math.max(0,raw.daily_limit-raw.daily_used)};
    }
    return {connected:false,reason:['login_required','customer_required'].includes(raw?.reason)?raw.reason:'unavailable',daily_limit:null,daily_used:null};
  }

  function safeResult(raw) {
    const messages = {
      subscribed:'已按用户中心规则提交订阅。', pending:'已提交，等待管理员审核。', duplicate:'用户中心已有这部影片的订阅记录。',
      uncertain:'提交结果待核查，请先到用户中心查看记录，勿重复提交。', error:'用户中心未完成本次订阅，请检查账号状态、额度或原有订阅记录。'
    };
    const status = typeof raw?.status === 'string' && Object.hasOwn(messages,raw.status) ? raw.status : 'uncertain';
    return {status,message:messages[status]};
  }

  window.addEventListener('message', event => {
    const data = event.data;
    if (!frame || event.origin !== CENTER || event.source !== frame.contentWindow || !data || data.channel !== CHANNEL || data.nonce !== nonce) return;
    if (data.type === 'ready') { if (readyResolve) readyResolve(); return; }
    if (data.type !== 'response' || typeof data.id !== 'string') return;
    const record = pending.get(data.id);
    if (!record) return;
    clearTimeout(record.timer); pending.delete(data.id);
    record.resolve(record.method === 'subscribe' ? safeResult(data.result) : safeStatus(data.result));
  });

  async function request(method,item) {
    const requestEpoch = epoch;
    try { await ensureFrame(); } catch { return method === 'subscribe' ? {status:'error',message:'用户中心暂未连接，请连接后再提交。'} : unavailable(); }
    if (requestEpoch !== epoch) return method === 'subscribe' ? {status:'error',message:'连接已结束，请重新连接用户中心。'} : unavailable();
    if (!frame || pending.size >= 8) return method === 'subscribe' ? {status:'error',message:'请等待当前请求完成。'} : unavailable();
    const id = `r${++counter}`;
    return new Promise(resolve => {
      const timer = setTimeout(() => { pending.delete(id); resolve(method === 'subscribe' ? uncertain() : unavailable()); }, method === 'subscribe' ? 80000 : 25000);
      pending.set(id,{method,resolve,timer});
      const safeItem = item && typeof item === 'object' ? {tmdb_id:item.tmdb_id??item.id,media_type:item.media_type??item.type,title:item.title} : null;
      frame.contentWindow.postMessage({channel:CHANNEL,nonce,type:'request',id,method,...(method === 'subscribe'?{item:safeItem}:{})},CENTER);
    });
  }

  window.MediaDockCustomerBridge = Object.freeze({
    configure: url => {
      try { const parsed = new URL(url || '', location.href); if (parsed.protocol !== 'https:') throw new Error('invalid'); CENTER = parsed.origin; }
      catch { CENTER = ''; reset(); }
    },
    connect:() => request('status'),
    status:() => request('status'),
    subscribe:item => request('subscribe',item),
    stop:reset
  });
}());
