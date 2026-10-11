'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const script = fs.readFileSync(path.join(__dirname, '../static/storefront.js'), 'utf8');

function markup(size = 3, id = 'home-carousel') {
  return `<section id="${id}" class="hero-carousel" role="region" tabindex="0">
    <div class="hero-deck">${Array.from({length: size}, (_, i) => `<article class="hero-slide" data-carousel-slide><h2>影片 ${i + 1}</h2><a href="#plan-${i}">了解</a></article>`).join('')}</div>
    <button data-carousel-prev>上一张</button><button data-carousel-next>下一张</button>
    ${Array.from({length: size}, (_, i) => `<button data-carousel-dot="${i}"></button>`).join('')}
    <button data-carousel-pause>暂停轮播</button><span class="carousel-count"></span>
    <input aria-label="测试输入框">
  </section>`;
}

function fixture({size = 3, reduced = false} = {}) {
  const dom = new JSDOM(`<main>${markup(size)}<button id="outside">外部按钮</button></main>`, {
    url: 'https://mg.example/', runScripts: 'outside-only', pretendToBeVisual: true
  });
  const win = dom.window;
  const timers = new Map();
  const motionListeners = new Set();
  let sequence = 0;
  let visibility = 'visible';
  let now = 0;
  let requests = 0;
  Object.defineProperty(win.document, 'visibilityState', {get: () => visibility, configurable: true});
  win.performance.now = () => now;
  win.setTimeout = (fn, ms) => {timers.set(++sequence, {fn, ms}); return sequence;};
  win.clearTimeout = id => timers.delete(id);
  const motion = {
    matches: reduced,
    addEventListener: (name, fn) => {if (name === 'change') motionListeners.add(fn);},
    removeEventListener: (name, fn) => {if (name === 'change') motionListeners.delete(fn);}
  };
  win.matchMedia = () => motion;
  win.fetch = () => {requests++; throw new Error('Carousel must not request data');};
  win.eval(script);
  const root = win.document.getElementById('home-carousel');
  const api = win.MediaDockCarousel;
  function fireTimer() {
    const entry = [...timers.entries()][0];
    assert.ok(entry, 'expected one autoplay timer');
    timers.delete(entry[0]);
    now += entry[1].ms;
    entry[1].fn();
  }
  function pointer(target, type, props = {}) {
    const event = new win.Event(type, {bubbles: true, cancelable: true});
    Object.assign(event, {pointerId: 1, isPrimary: true, button: 0, clientX: 0, clientY: 0}, props);
    target.dispatchEvent(event);
  }
  return {
    win, root, api, timers, motionListeners, fireTimer, pointer,
    slides: () => [...root.querySelectorAll('[data-carousel-slide]')],
    click: selector => root.querySelector(selector).click(),
    index: () => Number(root.dataset.carouselIndex),
    mouse: type => root.dispatchEvent(new win.MouseEvent(type)),
    key: (key, target = root, options = {}) => target.dispatchEvent(new win.KeyboardEvent('keydown', {key, bubbles: true, cancelable: true, ...options})),
    setVisibility(value) {visibility = value; win.document.dispatchEvent(new win.Event('visibilitychange'));},
    setReduced(value) {motion.matches = value; for (const fn of motionListeners) fn({matches: value});},
    advanceTime: ms => {now += ms;},
    requests: () => requests,
    close() {api.stop(); win.close();}
  };
}

test('initial carousel has one accessible slide and coherent positions, dots and count', t => {
  const f = fixture(); t.after(f.close);
  assert.equal(f.api.start(), true);
  assert.deepEqual(f.slides().map(s => s.dataset.position), ['active', 'next', 'previous']);
  assert.deepEqual(f.slides().map(s => s.getAttribute('aria-hidden')), ['false', 'true', 'true']);
  assert.deepEqual(f.slides().map(s => s.hasAttribute('inert')), [false, true, true]);
  assert.deepEqual(f.slides().map(s => s.inert), [false, true, true]);
  assert.equal(f.root.querySelector('.carousel-count').textContent, '01 / 03');
  assert.equal(f.root.querySelector('[aria-current="true"]').dataset.carouselDot, '0');
  assert.equal(f.root.querySelector('[data-carousel-dot="1"]').getAttribute('aria-label'), '切换到第 2 张');
  assert.equal(f.root.dataset.playing, 'true');
  assert.equal(f.timers.size, 1);
  assert.equal([...f.timers.values()][0].ms, 6000);
});

test('autoplay advances and wraps every six seconds with a single timer and no requests', t => {
  const f = fixture(); t.after(f.close); f.api.start();
  for (const expected of [1, 2, 0, 1]) {
    f.fireTimer();
    assert.equal(f.index(), expected);
    assert.equal(f.timers.size, 1);
    assert.equal([...f.timers.values()][0].ms, 6000);
  }
  assert.equal(f.requests(), 0);
});

test('arrows, dots and keyboard change slides and ignore editable or modified keys', t => {
  const f = fixture(); t.after(f.close); f.api.start();
  f.click('[data-carousel-prev]'); assert.equal(f.index(), 2);
  f.click('[data-carousel-next]'); assert.equal(f.index(), 0);
  f.click('[data-carousel-dot="2"]'); assert.equal(f.index(), 2);
  f.key('ArrowRight'); assert.equal(f.index(), 0);
  f.key('ArrowLeft'); assert.equal(f.index(), 2);
  f.key('ArrowRight', f.root.querySelector('input')); assert.equal(f.index(), 2);
  f.key('ArrowRight', f.root, {ctrlKey: true}); assert.equal(f.index(), 2);
  f.key('ArrowDown'); assert.equal(f.index(), 2);
  assert.equal(f.root.querySelector('[data-carousel-dot="2"]').getAttribute('aria-pressed'), 'true');
  assert.equal(f.root.querySelector('[data-carousel-dot="0"]').hasAttribute('aria-current'), false);
  assert.equal(f.timers.size, 1);
});

test('six and eight slide decks hide distant cards and wrap their neighbouring layers', t => {
  for (const size of [6, 8]) {
    const f = fixture({size}); t.after(f.close); f.api.start();
    assert.equal(f.slides().filter(s => s.dataset.position === 'hidden').length, size - 3);
    f.click(`[data-carousel-dot="${size - 1}"]`);
    assert.equal(f.slides()[size - 1].dataset.position, 'active');
    assert.equal(f.slides()[0].dataset.position, 'next');
    assert.equal(f.slides()[size - 2].dataset.position, 'previous');
    assert.equal(f.slides().filter(s => !s.hasAttribute('inert')).length, 1);
    assert.equal(f.root.querySelector('.carousel-count').textContent, `0${size} / 0${size}`);
    f.click('[data-carousel-next]'); assert.equal(f.index(), 0);
  }
});

test('hover and focus pause independently and leave resumes only when focus has left', t => {
  const f = fixture(); t.after(f.close); f.api.start();
  f.mouse('mouseenter'); assert.equal(f.timers.size, 0); assert.equal(f.root.dataset.playing, 'false');
  f.root.querySelector('[data-carousel-next]').focus();
  f.mouse('mouseleave'); assert.equal(f.timers.size, 0);
  f.root.querySelector('[data-carousel-prev]').focus(); assert.equal(f.timers.size, 0);
  f.win.document.getElementById('outside').focus(); assert.equal(f.timers.size, 1);
  f.mouse('mouseenter'); f.mouse('mouseleave'); assert.equal(f.timers.size, 1);
});

test('explicit pause survives hover, focus and page visibility and changes button action', t => {
  const f = fixture(); t.after(f.close); f.api.start();
  f.click('[data-carousel-pause]');
  assert.equal(f.timers.size, 0);
  assert.equal(f.root.querySelector('[data-carousel-pause]').textContent, '播放轮播');
  assert.equal(f.root.querySelector('[data-carousel-pause]').getAttribute('aria-label'), '播放轮播');
  f.mouse('mouseenter'); f.mouse('mouseleave');
  f.root.focus(); f.win.document.getElementById('outside').focus();
  f.setVisibility('hidden'); f.setVisibility('visible');
  assert.equal(f.timers.size, 0);
  f.click('[data-carousel-next]'); assert.equal(f.index(), 1); assert.equal(f.timers.size, 0);
  f.click('[data-carousel-pause]');
  assert.equal(f.timers.size, 1);
  assert.equal(f.root.querySelector('[data-carousel-pause]').getAttribute('aria-label'), '暂停轮播');
});

test('reduced motion defaults to paused but manual movement and an explicit play remain available', t => {
  const f = fixture({reduced: true}); t.after(f.close); f.api.start();
  assert.equal(f.timers.size, 0); assert.equal(f.root.dataset.playing, 'false');
  f.click('[data-carousel-next]'); assert.equal(f.index(), 1); assert.equal(f.timers.size, 0);
  f.click('[data-carousel-pause]'); assert.equal(f.timers.size, 1);
  f.fireTimer(); assert.equal(f.index(), 2);
  f.setReduced(false); assert.equal(f.timers.size, 1);
  f.setReduced(true); assert.equal(f.timers.size, 0);
  f.setReduced(false); assert.equal(f.timers.size, 1);
  f.click('[data-carousel-pause]'); f.setReduced(true); f.setReduced(false);
  assert.equal(f.timers.size, 0, 'system preference changes must not override user pause');
});

test('hidden page cancels autoplay and visible page receives a fresh interval', t => {
  const f = fixture(); t.after(f.close); f.api.start();
  f.setVisibility('hidden'); assert.equal(f.timers.size, 0); assert.equal(f.root.dataset.playing, 'false');
  f.advanceTime(60000); assert.equal(f.index(), 0);
  f.setVisibility('visible'); assert.equal(f.timers.size, 1);
  assert.equal([...f.timers.values()][0].ms, 6000);
  f.fireTimer(); assert.equal(f.index(), 1);
});

test('horizontal pointer swipes change slides without treating scrolling or clicks as swipes', t => {
  const f = fixture(); t.after(f.close); f.api.start();
  const deck = f.root.querySelector('.hero-deck');
  const swipe = (start, end, target = deck) => {
    f.pointer(target, 'pointerdown', start); f.pointer(f.win.document, 'pointerup', end);
  };
  swipe({clientX: 180, clientY: 30}, {clientX: 80, clientY: 40}); assert.equal(f.index(), 1);
  swipe({clientX: 50, clientY: 30}, {clientX: 150, clientY: 20}); assert.equal(f.index(), 0);
  swipe({clientX: 50}, {clientX: 75}); assert.equal(f.index(), 0);
  swipe({clientX: 50, clientY: 20}, {clientX: 120, clientY: 140}); assert.equal(f.index(), 0);
  swipe({clientX: 180, isPrimary: false}, {clientX: 50}); assert.equal(f.index(), 0);
  swipe({clientX: 180, button: 2}, {clientX: 50}); assert.equal(f.index(), 0);
  swipe({clientX: 180}, {clientX: 50}, f.slides()[0].querySelector('a')); assert.equal(f.index(), 0);
  f.pointer(deck, 'pointerdown', {clientX: 180}); f.advanceTime(1100);
  f.pointer(f.win.document, 'pointerup', {clientX: 50}); assert.equal(f.index(), 0);
  f.pointer(deck, 'pointerdown', {clientX: 180}); f.pointer(f.win.document, 'pointercancel');
  f.pointer(f.win.document, 'pointerup', {clientX: 50}); assert.equal(f.index(), 0);
});

test('manual navigation moves focus out of a card before making it inert', t => {
  const f = fixture(); t.after(f.close); f.api.start();
  f.slides()[0].querySelector('a').focus();
  f.key('ArrowRight', f.win.document.activeElement);
  assert.equal(f.index(), 1);
  assert.equal(f.win.document.activeElement, f.root);
  assert.equal(f.slides()[0].hasAttribute('inert'), true);
  assert.equal(f.timers.size, 0);
});

test('repeated start replaces all handlers and rebuilding the slide list is supported', t => {
  const f = fixture(); t.after(f.close);
  f.api.start(); f.api.start(); f.api.start();
  assert.equal(f.timers.size, 1); assert.equal(f.motionListeners.size, 1);
  f.click('[data-carousel-next]'); assert.equal(f.index(), 1);
  const replacement = f.win.document.createElement('div'); replacement.innerHTML = markup(6);
  f.root.innerHTML = replacement.firstElementChild.innerHTML;
  f.api.start(f.root); assert.equal(f.index(), 0);
  assert.equal(f.slides().length, 6);
  f.click('[data-carousel-prev]'); assert.equal(f.index(), 5);
  assert.equal(f.timers.size, 1); assert.equal(f.motionListeners.size, 1);
});

test('stop clears timers and listeners and prevents any later UI or lifecycle action', t => {
  const f = fixture(); t.after(f.close); f.api.start(); f.api.stop(); f.api.stop();
  assert.equal(f.timers.size, 0); assert.equal(f.motionListeners.size, 0);
  assert.equal(f.root.dataset.playing, 'false');
  f.click('[data-carousel-next]'); f.key('ArrowRight'); f.mouse('mouseleave');
  f.setVisibility('visible'); f.setReduced(false);
  f.win.dispatchEvent(new f.win.Event('pagehide'));
  f.win.dispatchEvent(new f.win.Event('pageshow'));
  assert.equal(f.index(), 0); assert.equal(f.timers.size, 0);
});

test('pagehide cleans up and pageshow restores one instance with its slide and pause choice', t => {
  const f = fixture(); t.after(f.close); f.api.start();
  f.click('[data-carousel-next]'); f.click('[data-carousel-pause]');
  f.win.dispatchEvent(new f.win.Event('pagehide'));
  assert.equal(f.timers.size, 0); assert.equal(f.motionListeners.size, 0);
  f.click('[data-carousel-next]'); assert.equal(f.index(), 1);
  f.win.dispatchEvent(new f.win.Event('pageshow'));
  assert.equal(f.index(), 1); assert.equal(f.timers.size, 0); assert.equal(f.motionListeners.size, 1);
  f.win.dispatchEvent(new f.win.Event('pageshow'));
  assert.equal(f.motionListeners.size, 1);
  f.mouse('mouseleave');
  f.click('[data-carousel-pause]'); assert.equal(f.timers.size, 1);
  f.fireTimer(); assert.equal(f.index(), 2);
});

test('stop after pagehide cancels restoration and detached carousels are never resurrected', t => {
  const f = fixture(); t.after(f.close); f.api.start();
  f.win.dispatchEvent(new f.win.Event('pagehide')); f.api.stop();
  f.win.dispatchEvent(new f.win.Event('pageshow'));
  assert.equal(f.timers.size, 0); assert.equal(f.motionListeners.size, 0);
  f.api.start(); f.win.dispatchEvent(new f.win.Event('pagehide')); f.root.remove();
  f.win.dispatchEvent(new f.win.Event('pageshow'));
  assert.equal(f.timers.size, 0); assert.equal(f.motionListeners.size, 0);
});

test('starting a different root detaches the previous one and an absent root is harmless', t => {
  const f = fixture(); t.after(f.close); f.api.start();
  f.root.insertAdjacentHTML('afterend', markup(3, 'second-carousel'));
  const second = f.win.document.getElementById('second-carousel');
  f.api.start(second); f.click('[data-carousel-next]'); assert.equal(f.index(), 0);
  second.querySelector('[data-carousel-next]').click(); assert.equal(second.dataset.carouselIndex, '1');
  assert.equal(f.root.dataset.playing, 'false'); assert.equal(f.timers.size, 1);
  f.root.remove(); assert.equal(f.api.start(), false); assert.equal(f.timers.size, 0);
});
