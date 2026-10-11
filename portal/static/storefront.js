(function () {
  'use strict';

  const INTERVAL_MS = 6000;
  let active = null;
  let resumeAfterPageShow = null;

  function stop() {
    if (active) active.dispose();
    active = null;
    if (resumeAfterPageShow) {
      window.removeEventListener('pageshow', resumeAfterPageShow);
      resumeAfterPageShow = null;
    }
  }

  function create(root, saved) {
    const slides = Array.from(root.querySelectorAll('[data-carousel-slide]'));
    if (!slides.length) return false;
    const dots = Array.from(root.querySelectorAll('[data-carousel-dot]'));
    const pauseButton = root.querySelector('[data-carousel-pause]');
    const count = root.querySelector('.carousel-count');
    const deck = root.querySelector('.hero-deck');
    const motion = typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    const removers = [];
    let index = saved ? saved.index % slides.length : 0;
    let manualPaused = !!(saved && saved.manualPaused);
    let allowReducedMotion = !!(saved && saved.allowReducedMotion);
    let reducedMotion = !!(motion && motion.matches);
    let hovering = root.matches(':hover');
    let focused = root.contains(document.activeElement);
    let timer = null;
    let gesture = null;
    let disposed = false;

    function listen(target, name, handler, options) {
      target.addEventListener(name, handler, options);
      removers.push(() => target.removeEventListener(name, handler, options));
    }

    function pausedByChoice() {
      return manualPaused || (reducedMotion && !allowReducedMotion);
    }

    function syncTimer() {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      const playing = !disposed && slides.length > 1 && root.isConnected
        && !pausedByChoice() && !hovering && !focused
        && document.visibilityState !== 'hidden';
      root.dataset.playing = String(playing);
      if (pauseButton) {
        const label = pausedByChoice() ? '播放轮播' : '暂停轮播';
        pauseButton.textContent = label;
        pauseButton.setAttribute('aria-label', label);
      }
      if (playing) {
        timer = window.setTimeout(() => {
          timer = null;
          if (!disposed) goTo(index + 1);
        }, INTERVAL_MS);
      }
    }

    function render() {
      root.dataset.carouselIndex = String(index);
      slides.forEach((slide, number) => {
        const offset = (number - index + slides.length) % slides.length;
        const current = offset === 0;
        slide.dataset.position = current ? 'active' : offset === 1 ? 'next'
          : offset === slides.length - 1 ? 'previous' : 'hidden';
        slide.setAttribute('aria-hidden', String(!current));
        slide.toggleAttribute('inert', !current);
        slide.inert = !current;
      });
      dots.forEach(dot => {
        const current = Number(dot.dataset.carouselDot) === index;
        dot.classList.toggle('is-active', current);
        dot.setAttribute('aria-pressed', String(current));
        if (current) dot.setAttribute('aria-current', 'true');
        else dot.removeAttribute('aria-current');
        if (!dot.hasAttribute('aria-label')) {
          dot.setAttribute('aria-label', `切换到第 ${Number(dot.dataset.carouselDot) + 1} 张`);
        }
      });
      if (count) count.textContent = `${String(index + 1).padStart(2, '0')} / ${String(slides.length).padStart(2, '0')}`;
    }

    function goTo(value) {
      if (disposed) return;
      const next = (value + slides.length) % slides.length;
      // A manual change must not leave keyboard focus inside a newly inert card.
      if (next !== index && slides[index].contains(document.activeElement)) {
        root.focus({preventScroll: true});
      }
      index = next;
      render();
      syncTimer();
    }

    function dispose() {
      if (disposed) return;
      disposed = true;
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      gesture = null;
      removers.splice(0).forEach(remove => remove());
      root.dataset.playing = 'false';
    }

    listen(root, 'click', event => {
      const control = event.target.closest && event.target.closest('[data-carousel-prev], [data-carousel-next], [data-carousel-dot], [data-carousel-pause]');
      if (!control || !root.contains(control)) return;
      event.preventDefault();
      if (control.hasAttribute('data-carousel-prev')) goTo(index - 1);
      else if (control.hasAttribute('data-carousel-next')) goTo(index + 1);
      else if (control.hasAttribute('data-carousel-pause')) {
        const shouldPlay = pausedByChoice();
        manualPaused = !shouldPlay;
        allowReducedMotion = shouldPlay;
        syncTimer();
      } else {
        const value = Number(control.dataset.carouselDot);
        if (Number.isInteger(value) && value >= 0 && value < slides.length) goTo(value);
      }
    });
    listen(root, 'keydown', event => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.target.closest && event.target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      goTo(index + (event.key === 'ArrowRight' ? 1 : -1));
    });
    listen(root, 'mouseenter', () => {hovering = true; syncTimer();});
    listen(root, 'mouseleave', () => {hovering = false; syncTimer();});
    listen(root, 'focusin', () => {focused = true; syncTimer();});
    listen(root, 'focusout', event => {
      focused = !!(event.relatedTarget && root.contains(event.relatedTarget));
      syncTimer();
    });
    listen(document, 'visibilitychange', () => {
      gesture = null;
      syncTimer();
    });

    if (deck) {
      listen(deck, 'pointerdown', event => {
        gesture = null;
        if (event.isPrimary === false || event.button !== 0) return;
        if (event.target.closest && event.target.closest('a, button, input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
        gesture = {id: event.pointerId, x: event.clientX, y: event.clientY, time: performance.now()};
      });
      listen(document, 'pointerup', event => {
        if (!gesture || event.pointerId !== gesture.id) return;
        const start = gesture;
        gesture = null;
        const x = event.clientX - start.x;
        const y = event.clientY - start.y;
        if (performance.now() - start.time <= 1000 && Math.abs(x) >= 45 && Math.abs(x) > Math.abs(y) * 1.25) {
          goTo(index + (x < 0 ? 1 : -1));
        }
      });
      listen(document, 'pointercancel', event => {
        if (gesture && event.pointerId === gesture.id) gesture = null;
      });
    }

    if (motion) {
      const motionChanged = event => {
        reducedMotion = !!event.matches;
        if (reducedMotion) allowReducedMotion = false;
        syncTimer();
      };
      if (motion.addEventListener) listen(motion, 'change', motionChanged);
      else if (motion.addListener) {
        motion.addListener(motionChanged);
        removers.push(() => motion.removeListener(motionChanged));
      }
    }

    const instance = {dispose, root, snapshot: () => ({index, manualPaused, allowReducedMotion})};
    listen(window, 'pagehide', () => {
      const savedState = {index, manualPaused, allowReducedMotion};
      dispose();
      if (active === instance) active = null;
      resumeAfterPageShow = () => {
        resumeAfterPageShow = null;
        if (root.isConnected) create(root, savedState);
      };
      window.addEventListener('pageshow', resumeAfterPageShow, {once: true});
    });

    active = instance;
    render();
    syncTimer();
    return true;
  }

  window.MediaDockCarousel = {
    start(root) {
      stop();
      const target = root || document.getElementById('home-carousel');
      if (!target || typeof target.querySelectorAll !== 'function') return false;
      return create(target);
    },
    // Rebind a refreshed feed without losing the visitor's pause or slide choice.
    refresh(root) {
      const saved = active && active.root === root ? active.snapshot() : null;
      stop();
      if (!root || !root.isConnected) return false;
      return create(root, saved);
    },
    stop
  };
})();
