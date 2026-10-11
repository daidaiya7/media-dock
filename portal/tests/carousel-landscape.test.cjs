'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const app=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/\nstart\(\);\s*$/,'');
const carousel=fs.readFileSync(path.join(__dirname,'../static/storefront.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function movie(id,backdrop){return {id,title:`电影 ${id}`,poster_url:`/api/public/featured-images/${String(id).padStart(64,'0')}.jpg`,url:`https://www.themoviedb.org/movie/${id}`,backdrop_url:backdrop};}
function fixture(t,{reduced=false}={}){
 const dom=new JSDOM('<div id="app"></div><div id="modal-root"></div><div id="toast-root"></div>',{url:'https://mg.example/',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,timers=new Map();let seq=0,complete;
 w.matchMedia=()=>({matches:reduced,addEventListener(){},removeEventListener(){}});
 w.setTimeout=(fn,ms)=>{timers.set(++seq,{fn,ms});return seq;};w.clearTimeout=id=>timers.delete(id);
 w.fetch=async url=>({ok:true,json:async()=>url.includes('featured')?await new Promise(resolve=>{complete=resolve;}):{products:[]}});
 w.eval(carousel);w.eval(app+'\nwindow.renderTestHome=renderShop;');
 w.renderTestHome();
 const root=w.document.querySelector('#home-carousel');
 t.after(()=>{w.MediaDockCarousel.stop();w.close();});
 return {w,root,timers,complete:async movies=>{await tick();complete({movies});await tick();await tick();}};
}
test('mixed feed prefers allowlisted landscape stills and retains posters for missing or untrusted backdrops',async t=>{
 const f=fixture(t);
 await f.complete([movie(1,'/api/public/featured-images/'+'a'.repeat(64)+'.jpg'),movie(2),movie(3,'https://image.tmdb.org.evil.example/t/p/w1280/a.jpg'),movie(4,'https://image.tmdb.org/t/p/w1280/a.jpg?key=private')]);
 const slides=[...f.root.querySelectorAll('[data-carousel-slide]')];
 assert.deepEqual(slides.map(x=>x.dataset.artwork),['backdrop','poster','poster','poster']);
 const image=slides[0].querySelector('img');
 assert.equal(image.getAttribute('src'),'/api/public/featured-images/'+'a'.repeat(64)+'.jpg');assert.equal(image.width,1280);assert.equal(image.height,720);
 assert.match(image.alt,/剧照/);
 for(const s of slides.slice(1)){assert.match(s.querySelector('img').src,/\/api\/public\/featured-images\//);}
 image.dispatchEvent(new f.w.Event('error'));
 assert.equal(image.hidden,true);assert.equal(image.getAttribute('src'),null);
 assert.equal(f.root.querySelectorAll('[aria-hidden="false"]').length,1);
});
test('delayed feed keeps selected slide and explicit pause, with working rebuilt controls',async t=>{
 const f=fixture(t);
 await f.complete(Array.from({length:6},(_,i)=>movie(i+1,`/api/public/featured-images/${String(i+30).padStart(64,'0')}.jpg`)));
 f.root.querySelector('[data-carousel-dot="2"]').click();f.root.querySelector('[data-carousel-pause]').click();
 assert.equal(f.root.dataset.carouselIndex,'2');assert.equal(f.root.dataset.playing,'false');
 assert.equal(f.root.querySelector('.carousel-count').textContent,'03 / 06');
 assert.equal(f.root.querySelector('[data-carousel-pause]').textContent,'播放轮播');
 assert.equal([...f.timers.values()].filter(x=>x.ms===6000).length,0);
 f.root.querySelector('[data-carousel-next]').click();assert.equal(f.root.dataset.carouselIndex,'3');
 f.root.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));assert.equal(f.root.dataset.carouselIndex,'2');
 f.root.querySelector('[data-carousel-pause]').click();assert.equal(f.root.dataset.playing,'true');
 const autoplay=[...f.timers.values()].filter(x=>x.ms===6000);assert.equal(autoplay.length,1);
 autoplay[0].fn();assert.equal(f.root.dataset.carouselIndex,'3');
});
test('explicit play choice under reduced motion survives the asynchronous feed replacement',async t=>{
 const f=fixture(t,{reduced:true});assert.equal(f.root.querySelectorAll('[data-carousel-slide]').length,0);
 await f.complete([movie(1),movie(2),movie(3)]);
 assert.equal(f.root.dataset.playing,'false');f.root.querySelector('[data-carousel-pause]').click();
 assert.equal(f.root.dataset.playing,'true');assert.equal(f.root.querySelector('[data-carousel-pause]').textContent,'暂停轮播');
});
