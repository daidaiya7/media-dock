/* Theme choice is per browser. Automatic mode uses the visitor's local clock. */
(function () {
  'use strict';
  const key = 'mediadock-theme-mode-v2';
  const configKey = 'mediadock-theme-schedule-v2';
  const modes = ['dark', 'light', 'auto'];
  let config = {mode: 'auto', day_start: '07:00', night_start: '19:00'};
  let preference = '';
  function read(key) { try { return window.localStorage.getItem(key); } catch { return null; } }
  function write(key, value) { try { window.localStorage.setItem(key, value); } catch { /* In-memory mode remains usable. */ } }
  function validTime(value) { return typeof value === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value); }
  function normalize(value) {
    value = value && typeof value === 'object' ? value : {};
    const mode = value.mode ?? value.theme_mode;
    const day = validTime(value.day_start) ? value.day_start : '07:00';
    const night = validTime(value.night_start) && value.night_start !== day ? value.night_start : (day === '19:00' ? '07:00' : '19:00');
    return {mode: modes.includes(mode) ? mode : 'auto', day_start: day, night_start: night};
  }
  try { config = normalize(JSON.parse(read(configKey))); } catch { /* Default before first catalog response. */ }
  preference = modes.includes(read(key)) ? read(key) : '';
  function selectedMode() { return preference || config.mode; }
  function minute(value) { const parts = value.split(':').map(Number); return parts[0] * 60 + parts[1]; }
  function resolve(mode, date = new Date()) {
    if (mode !== 'auto') return mode === 'light' ? 'light' : 'dark';
    const now = date.getHours() * 60 + date.getMinutes();
    const day = minute(config.day_start), night = minute(config.night_start);
    return (day < night ? now >= day && now < night : now >= day || now < night) ? 'light' : 'dark';
  }
  const names={auto:'自动',light:'白天',dark:'夜间'};
  const nextMode=mode=>({auto:'light',light:'dark',dark:'auto'}[mode]);
  function glyph(mode){
    const sun='<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>';
    const moon='<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z"/>';
    const auto='<circle cx="12" cy="12" r="6"/><path d="M12 6a6 6 0 0 1 0 12Z" fill="currentColor" stroke="none"/><path d="M12 1v2m0 18v2M1 12h2m18 0h2M4 4l1.5 1.5m13 13L20 20M4 20l1.5-1.5m13-13L20 4"/>';
    return '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(mode==='light'?sun:mode==='dark'?moon:auto)+'</svg>';
  }
  function label(mode){return '主题：'+names[mode]+'；点击切换为'+names[nextMode(mode)];}
  function syncControls() {
    document.querySelectorAll('[data-theme-toggle]').forEach(button=>{const mode=selectedMode();button.dataset.themeMode=mode;button.innerHTML=glyph(mode);button.title=label(mode);button.setAttribute('aria-label',label(mode));});
  }
  function apply() {
    const theme = resolve(selectedMode());
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.themeMode = selectedMode();
    document.documentElement.style.colorScheme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === 'light' ? '#f7f5ef' : '#0b0b0c';
    syncControls();
    return theme;
  }
  function configure(value) {
    config = normalize(value?.theme || value);
    write(configKey, JSON.stringify(config));
    apply();
  }
  function setMode(value) {
    if (!modes.includes(value)) return false;
    preference = value;
    write(key, value);
    apply();
    return true;
  }
  function control() {
    const mode=selectedMode();
    return '<button type="button" class="theme-switch" data-theme-toggle data-theme-mode="'+mode+'" title="'+label(mode)+'" aria-label="'+label(mode)+'">'+glyph(mode)+'</button>';
  }
  document.addEventListener('click',event=>{if(event.target.closest?.('[data-theme-toggle]'))setMode(nextMode(selectedMode()));});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) apply(); });
  window.addEventListener('pageshow', apply);
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) { preference = modes.includes(read(key)) ? read(key) : ''; apply(); }
  });
  window.setInterval(() => { if (selectedMode() === 'auto') apply(); }, 30000);
  window.MediaDockTheme = Object.freeze({configure, control, controlMarkup: control, setMode, apply, resolve});
  apply();
}());
