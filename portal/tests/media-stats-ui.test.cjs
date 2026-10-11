'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(path.join(__dirname,'../static/media-stats.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
const stats=(overrides={})=>({configured:true,state:'ready',counts:{movies:1200,series:240,episodes:15200},library_count:16,quality:{denominator:1200,uhd:600,hdr:720,dolby_vision:180,remux:300},updated_at:'2026-10-11T12:00:00Z',stale:false,...overrides});
const typedStats=(overrides={})=>stats({counts:{movies:100,series:90,episodes:900},quality_by_type:{movies:{denominator:100,uhd:80,hdr:70,dolby_vision:50,remux:40,remux_unknown:5},episodes:{denominator:900,uhd:100,hdr:200,dolby_vision:70,remux:20,remux_unknown:10},all:{denominator:1000,uhd:180,hdr:270,dolby_vision:120,remux:60,remux_unknown:15}},...overrides});
function fixture({route='/library',handler=()=>response(stats()),mount=true}={}){
 const dom=new JSDOM('<main></main>',{url:'https://media.example.com'+route,runScripts:'outside-only'}),win=dom.window,calls=[],timers=new Map();let counter=0;
 win.setTimeout=(fn,ms)=>{const id=++counter;timers.set(id,{fn,ms});return id;};win.clearTimeout=id=>timers.delete(id);
 win.fetch=(url,options)=>{calls.push({url,options});return Promise.resolve(handler(url,options));};win.eval(source);
 const api=win.MediaDockMediaStats,root=win.document.querySelector('main');root.innerHTML=api.markup();if(mount)api.mount(root);
 return{win,api,root,calls,timers,element:root.querySelector('[data-media-stats]'),fire(ms){const entry=[...timers].find(([,value])=>value.ms===ms);assert(entry,`missing ${ms} timer`);timers.delete(entry[0]);entry[1].fn();},close(){api.stop();win.close();}};
}

test('library shows aggregate counts and movie-only ratios without exposing unknown response fields',async()=>{
 const f=fixture({handler:()=>response(stats({server_url:'https://hidden.internal',token:'PRIVATE',items:[{title:'Hidden title'}]}))});try{await tick();assert.equal(f.calls.length,1);assert.equal(f.calls[0].url,'/api/public/library/stats');assert.equal(f.calls[0].options.cache,'no-store');assert.equal(f.element.hidden,false);const numbers=[...f.element.querySelectorAll('.media-stats-total strong')].map(el=>el.textContent);assert.deepEqual(numbers,['1,200部','240部','15,200集']);assert.deepEqual([...f.element.querySelectorAll('progress')].map(el=>el.value),[50,60,15,25]);assert.match(f.element.textContent,/电影 · 分母 1,200 部/);assert(!f.element.textContent.includes('Hidden title'));assert(!f.element.innerHTML.includes('PRIVATE'));assert(!f.element.innerHTML.includes('hidden.internal'));assert.match(f.element.textContent,/16 个开放媒体库/);assert.equal([...f.timers.values()].filter(t=>t.ms===300000).length,1);}finally{f.close();}
});

test('typed statistics default to all movie and single-episode video entries in a compact bar',async()=>{
 const f=fixture({handler:()=>response(typedStats())});try{await tick();assert.equal(f.element.querySelector('[data-media-stats-scope="all"]').getAttribute('aria-pressed'),'true');assert.deepEqual([...f.element.querySelectorAll('.media-stats-total strong')].map(el=>el.textContent),['100部','90部','900集']);assert.deepEqual([...f.element.querySelectorAll('progress')].map(el=>el.value),[18,27,12,6]);assert.equal(f.element.querySelector('.media-stats-denominator').textContent,'电影 + 单集 · 分母 1,000 个视频条目');assert.deepEqual([...f.element.querySelectorAll('.media-stats-quality>p')].map(el=>el.textContent),['180 个','270 个','120 个','≥ 60 个']);assert.equal(f.element.querySelector('.media-stats-quality-note').textContent,'已识别 · 15 个文件名待补充');assert.match(f.element.querySelector('details').textContent,/不将整部剧重复计入/);assert.equal(f.element.querySelector('h2'),null);assert(!f.element.textContent.includes('INSIDE THE LIBRARY'));assert(!f.element.textContent.includes('光影，汇聚于此'));}finally{f.close();}
});

test('scope switches use their own denominators and units without refetching or changing totals',async()=>{
 const f=fixture({handler:()=>response(typedStats())});try{await tick();f.element.querySelector('[data-media-stats-scope="episodes"]').click();assert.equal(f.element.querySelector('[data-media-stats-scope="episodes"]').getAttribute('aria-pressed'),'true');assert.equal(f.element.querySelector('.media-stats-denominator').textContent,'剧集按单集统计 · 分母 900 集');assert.deepEqual([...f.element.querySelectorAll('progress')].map(el=>el.value),[11.1,22.2,7.8,2.2]);assert.equal(f.element.querySelector('.media-stats-quality>p').textContent,'100 集');assert.equal(f.element.querySelector('progress').getAttribute('aria-label'),'4K UHD 占单集 11.1%');assert.equal(f.element.querySelector('.media-stats-quality-note').textContent,'已识别 · 10 集文件名待补充');f.element.querySelector('[data-media-stats-scope="movies"]').click();assert.equal(f.element.querySelector('.media-stats-denominator').textContent,'电影 · 分母 100 部');assert.deepEqual([...f.element.querySelectorAll('progress')].map(el=>el.value),[80,70,50,40]);assert.equal(f.element.querySelector('.media-stats-quality>p').textContent,'80 部');f.element.querySelector('[data-media-stats-scope="all"]').click();assert.equal(f.element.querySelector('.media-stats-quality>p').textContent,'180 个');assert.deepEqual([...f.element.querySelectorAll('.media-stats-total strong')].map(el=>el.textContent),['100部','90部','900集']);assert.equal(f.calls.length,1);}finally{f.close();}
});

test('legacy and malformed typed payloads only enable the movie scope and never imply combined coverage',async()=>{
 for(const typed of [undefined,null,[],{all:null,episodes:[],movies:42}]){const f=fixture({handler:()=>response(stats({quality_by_type:typed}))});try{await tick();assert.equal(f.element.querySelector('[data-media-stats-scope="movies"]').getAttribute('aria-pressed'),'true');assert.equal(f.element.querySelector('[data-media-stats-scope="all"]').disabled,true);assert.equal(f.element.querySelector('[data-media-stats-scope="episodes"]').disabled,true);assert.match(f.element.querySelector('.media-stats-denominator').textContent,/仅有电影统计/);f.element.querySelector('[data-media-stats-scope="all"]').click();assert.equal(f.element.querySelector('.media-stats-quality>p').textContent,'600 部');assert.equal(f.calls.length,1);}finally{f.close();}}
});

test('unknown typed values never borrow legacy movie statistics or compute a fabricated combined total',async()=>{
 const f=fixture({handler:()=>response(typedStats({quality_by_type:{all:{denominator:null,uhd:50,hdr:null,dolby_vision:10,remux:0,remux_unknown:null},episodes:{denominator:900,uhd:901,hdr:null,dolby_vision:'70',remux:0,remux_unknown:900}}}))});try{await tick();assert.equal(f.element.querySelector('[data-media-stats-scope="all"]').getAttribute('aria-pressed'),'true');assert.equal(f.element.querySelectorAll('progress').length,0);assert.deepEqual([...f.element.querySelectorAll('.media-stats-quality>p')].map(el=>el.textContent),['— 个','— 个','— 个','— 个']);assert.match(f.element.querySelector('.media-stats-denominator').textContent,/分母 待统计/);f.element.querySelector('[data-media-stats-scope="episodes"]').click();assert.equal(f.element.querySelectorAll('progress').length,0);assert.deepEqual([...f.element.querySelectorAll('.media-stats-quality>p')].map(el=>el.textContent),['— 集','— 集','— 集','— 集']);assert.equal(f.element.querySelector('.media-stats-quality-note').textContent,'900 集文件名待补充');}finally{f.close();}
});

test('scope and keyboard focus survive refresh, with retained scope-labelled data on a read failure',async()=>{
 let good=true;const f=fixture({handler:()=>good?response(typedStats()):Promise.reject(new Error('offline'))});try{await tick();f.element.querySelector('[data-media-stats-scope="episodes"]').click();f.element.querySelector('details').open=true;f.fire(300000);await tick();assert.equal(f.win.document.activeElement.dataset.mediaStatsScope,'episodes');assert.equal(f.element.querySelector('details').open,true);assert.equal(f.element.querySelector('[data-media-stats-scope="episodes"]').getAttribute('aria-pressed'),'true');good=false;f.fire(300000);await tick();assert.match(f.element.querySelector('.media-stats-source').textContent,/最近一次统计/);assert.equal(f.element.querySelector('.media-stats-quality>p').textContent,'100 集');assert.equal(f.element.querySelector('[data-media-stats-scope="episodes"]').getAttribute('aria-pressed'),'true');}finally{f.close();}
});

test('an initial legacy snapshot upgrades to all by default but preserves an explicit movie selection',async()=>{
 for(const explicit of [false,true]){let upgraded=false;const f=fixture({handler:()=>response(upgraded?typedStats():stats())});try{await tick();if(explicit)f.element.querySelector('[data-media-stats-scope="movies"]').click();upgraded=true;f.fire(300000);await tick();assert.equal(f.element.querySelector(`[data-media-stats-scope="${explicit?'movies':'all'}"]`).getAttribute('aria-pressed'),'true');assert.equal(f.element.querySelector('.media-stats-quality>p').textContent,explicit?'80 部':'180 个');}finally{f.close();}}
});

test('stop removes scope controls and a later click cannot update the stopped view',async()=>{
 const f=fixture({handler:()=>response(typedStats())});try{await tick();const before=f.element.innerHTML;f.api.stop();f.element.querySelector('[data-media-stats-scope="episodes"]').click();assert.equal(f.element.innerHTML,before);assert.equal(f.calls.length,1);assert.equal(f.timers.size,0);}finally{f.close();}
});

test('quality uses the movie denominator rather than episode or series counts and overlaps are allowed',async()=>{
 const f=fixture({handler:()=>response(stats({counts:{movies:100,series:900,episodes:9000},quality:{denominator:100,uhd:80,hdr:70,dolby_vision:50,remux:40}}))});try{await tick();assert.deepEqual([...f.element.querySelectorAll('progress')].map(el=>el.value),[80,70,50,40]);assert.match(f.element.textContent,/画质与版本可重叠，比例不相加/);assert.deepEqual([...f.element.querySelectorAll('.media-stats-quality>p')].map(el=>el.textContent),['80 部','70 部','50 部','40 部']);}finally{f.close();}
});

test('HDR is an independent movie statistic beside 4K, Dolby Vision and REMUX',async()=>{
 const f=fixture({handler:()=>response(stats({counts:{movies:100,series:500,episodes:5000},quality:{denominator:100,uhd:80,hdr:75,dolby_vision:50,remux:40}}))});try{await tick();const cards=[...f.element.querySelectorAll('.media-stats-quality')];assert.equal(cards.length,4);assert.deepEqual(cards.map(card=>card.querySelector('h4').firstChild.textContent),['4K UHD','HDR','Dolby Vision','REMUX']);const hdr=cards[1];assert.equal(hdr.querySelector('h4 small').textContent,'高动态范围');assert.equal(hdr.querySelector('strong').textContent,'75.0%');assert.equal(hdr.querySelector('p').textContent,'75 部');assert.equal(hdr.querySelector('progress').value,75);assert.equal(hdr.querySelector('progress').getAttribute('aria-label'),'HDR 占电影 75.0%');assert.match(f.element.querySelector('details').textContent,/HDR 包含 Emby 识别的 HDR10、HDR10\+、HLG 和杜比视界/);assert.match(f.element.querySelector('details').textContent,/各标签独立统计；画质与版本可重叠，比例不相加/);}finally{f.close();}
});

test('HDR absent, null or invalid remains unknown while a measured zero is shown honestly',async()=>{
 for(const value of [undefined,null,-1,101,'75','<img onerror=bad()>',0]){const quality={denominator:100,uhd:50,dolby_vision:20,remux:10};if(value!==undefined)quality.hdr=value;const f=fixture({handler:()=>response(stats({quality}))});try{await tick();const hdr=f.element.querySelectorAll('.media-stats-quality')[1];assert.equal(hdr.querySelector('strong').textContent,value===0?'0.0%':'待统计');assert.equal(hdr.querySelector('p').textContent,value===0?'0 部':'— 部');assert.equal(hdr.querySelectorAll('progress').length,value===0?1:0);assert.equal(hdr.querySelector('img'),null);assert.equal(f.element.querySelectorAll('.media-stats-quality')[0].querySelector('strong').textContent,'50.0%');}finally{f.close();}}
});

test('partially identified REMUX uses lower-bound amounts and percentages without changing native quality counts',async()=>{
 const f=fixture({handler:()=>response(stats({quality:{denominator:1200,uhd:600,hdr:720,dolby_vision:180,remux:300,remux_unknown:20}}))});try{await tick();const cards=[...f.element.querySelectorAll('.media-stats-quality')];assert.deepEqual(cards.map(el=>el.querySelector('strong').textContent),['50.0%','60.0%','15.0%','≥ 25.0%']);assert.equal(cards[3].querySelector('p').textContent,'≥ 300 部');assert.equal(cards[3].querySelector('.media-stats-quality-note').textContent,'已识别 · 20 部文件名待补充');assert.equal(cards[3].querySelector('progress').getAttribute('aria-label'),'REMUX 至少占电影 25.0%');assert.equal(cards[0].querySelector('.media-stats-quality-note'),null);assert.equal(cards[1].querySelector('.media-stats-quality-note'),null);assert.match(f.element.querySelector('details').textContent,/下限/);}finally{f.close();}
});

test('REMUX lower-bound percentage rounds down rather than overstating the identified share',async()=>{
 const f=fixture({handler:()=>response(stats({quality:{denominator:3,uhd:2,hdr:2,dolby_vision:2,remux:2,remux_unknown:1}}))});try{await tick();assert.deepEqual([...f.element.querySelectorAll('.media-stats-quality-label>strong')].map(el=>el.textContent),['66.7%','66.7%','66.7%','≥ 66.6%']);assert.equal(f.element.querySelectorAll('progress')[3].value,66.6);}finally{f.close();}
});

test('REMUX without unknown filenames remains exact for zero and omitted compatibility fields',async()=>{
 for(const extra of [{remux_unknown:0},{}]){const f=fixture({handler:()=>response(stats({quality:{denominator:1200,uhd:600,hdr:720,dolby_vision:180,remux:300,...extra}}))});try{await tick();const remux=f.element.querySelectorAll('.media-stats-quality')[3];assert.equal(remux.querySelector('strong').textContent,'25.0%');assert.equal(remux.querySelector('p').textContent,'300 部');assert.equal(remux.querySelector('.media-stats-quality-note'),null);assert(!f.element.textContent.includes('≥'));}finally{f.close();}}
});

test('partially known filenames with no identified REMUX show a lower bound while wholly unknown filenames stay pending',async()=>{
 for(const unknown of [20,100]){const f=fixture({handler:()=>response(stats({quality:{denominator:100,uhd:50,dolby_vision:10,remux:0,remux_unknown:unknown}}))});try{await tick();const remux=f.element.querySelectorAll('.media-stats-quality')[3];assert.equal(remux.querySelector('strong').textContent,unknown===100?'待统计':'≥ 0.0%');assert.equal(remux.querySelector('p').textContent,unknown===100?'— 部':'≥ 0 部');assert.equal(remux.querySelector('.media-stats-quality-note').textContent,unknown===100?'100 部文件名待补充':'已识别 · 20 部文件名待补充');assert.equal(remux.querySelectorAll('progress').length,unknown===100?0:1);}finally{f.close();}}
});

test('REMUX unknown amounts and invalid filename counts never fabricate a known statistic',async()=>{
 for(const quality of [{denominator:100,remux:null,remux_unknown:20},{denominator:null,remux:30,remux_unknown:20},{denominator:100,remux:30,remux_unknown:null},{denominator:100,remux:30,remux_unknown:'<img onerror=bad()>'},{denominator:100,remux:30,remux_unknown:101}]){const f=fixture({handler:()=>response(stats({quality}))});try{await tick();const remux=f.element.querySelectorAll('.media-stats-quality')[3];assert.equal(remux.querySelector('strong').textContent,'待统计');assert.equal(remux.querySelectorAll('progress').length,0);assert.match(remux.querySelector('p').textContent,/^— 部/);assert.equal(f.element.querySelector('img'),null);assert(!f.element.textContent.includes('0.0%'));}finally{f.close();}}
});

test('stale statistics retain REMUX lower-bound markers and unknown filename counts',async()=>{
 let good=true;const f=fixture({handler:()=>good?response(stats({quality:{denominator:1200,uhd:600,hdr:720,dolby_vision:180,remux:300,remux_unknown:20}})):Promise.reject(new Error('offline'))});try{await tick();good=false;f.fire(300000);await tick();const remux=f.element.querySelectorAll('.media-stats-quality')[3];assert.match(f.element.textContent,/最近一次统计/);assert.equal(remux.querySelector('strong').textContent,'≥ 25.0%');assert.equal(remux.querySelector('.media-stats-quality-note').textContent,'已识别 · 20 部文件名待补充');}finally{f.close();}
});

test('unknown and invalid counts remain unknown; quality above denominator never fabricates a ratio',async()=>{
 const f=fixture({handler:()=>response(stats({counts:{movies:null,series:-1,episodes:'200'},quality:{denominator:100,uhd:null,dolby_vision:101,remux:NaN},library_count:-1}))});try{await tick();assert.deepEqual([...f.element.querySelectorAll('.media-stats-total strong')].map(el=>el.textContent),['—部','—部','—集']);assert.equal(f.element.querySelectorAll('progress').length,0);assert.equal(f.element.querySelectorAll('.media-stats-track.is-pending').length,4);assert(!f.element.textContent.includes('NaN'));assert(!f.element.textContent.includes('undefined'));assert(!f.element.textContent.includes('0.0%'));assert.equal(f.element.querySelector('.media-stats-source').textContent,'已同步 Emby');}finally{f.close();}
});

test('zero movies is a valid empty library with no divide-by-zero percentages',async()=>{
 const f=fixture({handler:()=>response(stats({counts:{movies:0,series:0,episodes:0},quality:{denominator:0,uhd:0,hdr:0,dolby_vision:0,remux:0}}))});try{await tick();assert.equal(f.element.querySelectorAll('progress').length,0);assert.deepEqual([...f.element.querySelectorAll('.media-stats-quality-label>strong')].map(el=>el.textContent),['暂无电影','暂无电影','暂无电影','暂无电影']);assert(!f.element.textContent.includes('Infinity'));assert.match(f.element.querySelector('.media-stats-denominator').textContent,/分母 0 部/);}finally{f.close();}
});

test('unknown denominator suppresses known quality amounts rather than claiming totals',async()=>{
 const f=fixture({handler:()=>response(stats({quality:{denominator:null,uhd:600,hdr:720,dolby_vision:180,remux:300}}))});try{await tick();assert.equal(f.element.querySelectorAll('progress').length,0);assert.deepEqual([...f.element.querySelectorAll('.media-stats-quality>p')].map(el=>el.textContent),['— 部','— 部','— 部','— 部']);assert.equal(f.element.querySelectorAll('.media-stats-quality-label>strong')[0].textContent,'待统计');}finally{f.close();}
});

test('statistical explanation uses valid sibling details and time structure',async()=>{
 const f=fixture();try{await tick();const foot=f.element.querySelector('.media-stats-foot');assert.equal(foot.children.length,3);assert.equal(foot.children[0].className,'media-stats-notes');assert.equal(foot.children[1].tagName,'SPAN');assert.equal(foot.querySelector('details').parentElement.className,'media-stats-notes');assert.equal(foot.querySelector('p details'),null);assert.equal(foot.querySelectorAll(':scope > p').length,0);assert.match(foot.textContent,/剧集以每一集为分母/);}finally{f.close();}
});

test('unconfigured response hides and clears any previous statistics',async()=>{
 let configured=true;const f=fixture({handler:()=>response(configured?stats():{configured:false,counts:{movies:999}})});try{await tick();assert.equal(f.element.hidden,false);configured=false;f.fire(300000);await tick();assert.equal(f.element.hidden,true);assert.equal(f.element.innerHTML,'');f.api.setVisible(true);assert.equal(f.element.hidden,true);}finally{f.close();}
});

test('first synchronization displays unknown figures and refreshes after fifteen seconds',async()=>{
 let done=false;const f=fixture({handler:()=>response(done?stats():stats({state:'syncing',counts:{},quality:{},updated_at:null}))});try{await tick();assert.match(f.element.textContent,/正在同步统计/);assert.match(f.element.textContent,/首次统计完成后自动显示/);assert.equal(f.element.querySelectorAll('progress').length,0);done=true;f.fire(15000);await tick();assert.match(f.element.textContent,/已同步 Emby/);assert.equal(f.calls.length,2);}finally{f.close();}
});

test('temporary read failure preserves verified counts with an explicit stale label',async()=>{
 let good=true;const f=fixture({handler:()=>good?response(stats()):Promise.reject(new Error('secret server.internal'))});try{await tick();good=false;f.fire(300000);await tick();assert.match(f.element.textContent,/最近一次统计/);assert.match(f.element.textContent,/1,200/);assert.match(f.element.querySelector('.media-stats-updated').textContent,/上次/);assert(!f.element.innerHTML.includes('server.internal'));assert([...f.timers.values()].some(t=>t.ms===60000));}finally{f.close();}
});

test('first read failure shows honest unavailable placeholders without zeroing totals',async()=>{
 const f=fixture({handler:()=>response({error:'private upstream'},503)});try{await tick();assert.equal(f.element.hidden,false);assert.match(f.element.textContent,/统计暂时不可用/);assert.deepEqual([...f.element.querySelectorAll('.media-stats-total strong')].map(el=>el.textContent),['—部','—部','—集']);assert(!f.element.textContent.includes('private upstream'));assert([...f.timers.values()].some(t=>t.ms===60000));}finally{f.close();}
});

test('malformed state and unsafe dates never render arbitrary HTML or corrupt retained data',async()=>{
 let bad=false;const f=fixture({handler:()=>response(bad?stats({state:'<img onerror=x>'}):stats({updated_at:'<img src=x onerror=boom()>',stale:'false'}))});try{await tick();assert.equal(f.element.querySelector('img'),null);assert.equal(f.element.querySelector('.media-stats-source').textContent,'16 个开放媒体库 · 已同步 Emby');assert.equal(f.element.querySelector('.media-stats-updated').textContent,'稍后自动更新');bad=true;f.fire(300000);await tick();assert.match(f.element.textContent,/最近一次统计/);assert.equal(f.element.querySelector('img'),null);}finally{f.close();}
});

test('statistics are hidden in a selected library or search and can return to the index',async()=>{
 for(const route of ['/library?library=a','/library?query=movie']){const f=fixture({route});try{await tick();assert.equal(f.element.hidden,true);f.api.setVisible(true);assert.equal(f.element.hidden,false);f.api.setVisible(false);assert.equal(f.element.hidden,true);}finally{f.close();}}
});

test('home and discovery never fetch or mount statistics even if placeholder is accidentally supplied',async()=>{
 for(const route of ['/','/discover','/admin']){const f=fixture({route});try{await tick();assert.equal(f.calls.length,0);assert.equal(f.element.hidden,true);assert.equal(f.element.innerHTML,'');assert.equal(f.timers.size,0);}finally{f.close();}}
});

test('stop cancels the pending fetch and every timer; late response cannot update removed view',async()=>{
 let resolve;const f=fixture({handler:()=>new Promise(yes=>{resolve=yes;})});try{const signal=f.calls[0].options.signal;assert.equal(f.timers.size,1);f.api.stop();assert.equal(signal.aborted,true);assert.equal(f.timers.size,0);resolve(response(stats()));await tick();assert.equal(f.element.hidden,true);assert.equal(f.element.innerHTML,'');assert.equal(f.timers.size,0);}finally{f.close();}
});

test('remount rejects older successful response and uses only the new active view',async()=>{
 const pending=[];const f=fixture({handler:()=>new Promise(resolve=>pending.push(resolve))});try{f.api.mount(f.root);pending[1](response(stats({counts:{movies:5,series:6,episodes:7}})));await tick();pending[0](response(stats({counts:{movies:999,series:999,episodes:999}})));await tick();assert.equal(f.element.querySelector('.media-stats-total strong').textContent,'5部');assert.equal(f.calls[0].options.signal.aborted,true);assert.equal([...f.timers.values()].filter(t=>t.ms===300000).length,1);}finally{f.close();}
});
