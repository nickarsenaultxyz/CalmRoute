/**
 * Progressive data loading.
 *
 * The manifest drives every other fetch, so no path is hardcoded and a rebuild
 * can rename or version files freely.
 *
 * Only `network.geojson` is on the critical path (~246 KB gzipped). The
 * residential bulk (~358 KB) arrives on idle or when the map zooms past 13 --
 * at the default city-wide zoom those 8,900 quiet streets are visual mush, so
 * deferring them costs nothing visible.
 */

import { DATA_DIR } from './config.js';

const cache = new Map();

async function getJSON(path) {
  if (cache.has(path)) return cache.get(path);
  const p = fetch(path).then((r) => {
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  });
  cache.set(path, p);
  return p;
}

export async function loadManifest() {
  const m = await getJSON(`${DATA_DIR}manifest.json`);
  m._url = (key) => `${DATA_DIR}${m.files[key]}?v=${encodeURIComponent(m.version)}`;
  return m;
}

export const loadStats = (m) => getJSON(m._url('stats'));
export const loadMethodology = (m) => getJSON(m._url('methodology'));
export const loadNetwork = (m) => getJSON(m._url('network'));
export const loadContext = (m) => getJSON(m._url('context'));
export const loadIslands = (m) => getJSON(m._url('islands'));
export const loadGaps = (m) => getJSON(m._url('gaps'));
export const loadGraph = (m) => getJSON(m._url('graph'));
export const loadPlanned = (m) => getJSON(m._url('planned'));

/**
 * Fetch the residential layer once, on whichever trigger fires first.
 * Returns a promise so callers can await readiness without re-triggering.
 */
export function deferResidential(map, manifest, { onStart, onDone } = {}) {
  let started = null;

  const run = () => {
    if (started) return started;
    onStart?.();
    started = getJSON(manifest._url('residential'))
      .then((fc) => {
        map.getSource('residential')?.setData(fc);
        onDone?.(fc);
        return fc;
      })
      .catch((err) => {
        console.error('residential layer failed to load', err);
        onDone?.(null);
        return null;
      });
    return started;
  };

  if ('requestIdleCallback' in window) {
    requestIdleCallback(run, { timeout: 4000 });
  } else {
    setTimeout(run, 1200);
  }
  map.on('zoom', () => { if (map.getZoom() >= 13) run(); });

  return { ready: () => run() };
}
