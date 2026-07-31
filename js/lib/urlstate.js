/**
 * URL state.
 *
 * `replaceState` for continuous changes (map movement, debounced) so panning
 * does not fill the history stack; `pushState` for discrete ones (selecting a
 * segment, opening a view) so the browser back button behaves the way a phone
 * user expects. That distinction is the whole design.
 */

/**
 * Residential streets default OFF.
 *
 * They are 8,908 of 14,169 features and they swamp the map: at any zoom the
 * page reads as a wall of blue, which makes route conditions and dedicated bike
 * facilities harder to distinguish. Off by default, one tap to bring back.
 */
const DEFAULTS = { view: 'route', basemap: 'light', residential: false };

export function read() {
  const p = new URLSearchParams(location.search);
  const state = {
    view: p.get('view') || DEFAULTS.view,
    basemap: p.get('bm') || DEFAULTS.basemap,
    residential: p.has('res') ? p.get('res') === '1' : DEFAULTS.residential,
    selected: p.has('sel') ? Number(p.get('sel')) : null,
    lts: p.has('lts')
      ? new Set(p.get('lts').split(',').map(Number).filter((n) => !Number.isNaN(n)))
      : new Set([0, 1, 2, 3, 4]),
  };
  if (p.has('c')) {
    const [lng, lat] = p.get('c').split(',').map(Number);
    if (Number.isFinite(lng) && Number.isFinite(lat)) state.center = [lng, lat];
  }
  if (p.has('z')) {
    const z = Number(p.get('z'));
    if (Number.isFinite(z)) state.zoom = z;
  }
  return state;
}

export function write(state, { push = false } = {}) {
  const p = new URLSearchParams();
  if (state.center) {
    p.set('c', `${state.center[0].toFixed(4)},${state.center[1].toFixed(4)}`);
  }
  if (state.zoom != null) p.set('z', state.zoom.toFixed(1));
  if (state.lts && state.lts.size < 5) {
    p.set('lts', [...state.lts].sort((a, b) => a - b).join(','));
  }
  if (state.residential !== DEFAULTS.residential) p.set('res', state.residential ? '1' : '0');
  if (state.view && state.view !== DEFAULTS.view) p.set('view', state.view);
  if (state.selected != null) p.set('sel', String(state.selected));
  if (state.basemap && state.basemap !== DEFAULTS.basemap) p.set('bm', state.basemap);

  const url = `${location.pathname}${p.toString() ? `?${p}` : ''}`;
  history[push ? 'pushState' : 'replaceState'](null, '', url);
}

export function onPopState(handler) {
  window.addEventListener('popstate', () => handler(read()));
}

export function debounce(fn, ms) {
  let t = 0;
  return (...args) => {
    clearTimeout(t);
    t = window.setTimeout(() => fn(...args), ms);
  };
}
