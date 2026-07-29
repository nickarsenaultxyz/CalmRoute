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
  // The manifest is tiny and tells every other request which build to load.
  // Give it a per-page URL so a browser or the Pages CDN cannot pair a fresh
  // frontend with yesterday's artifact version.
  const m = await getJSON(`${DATA_DIR}manifest.json?v=${Date.now()}`);
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
export const loadCouncil = (m) => getJSON(m._url('council'));

/**
 * Fetch the residential layer at most once.
 *
 * Because it is off by default, this is NOT prefetched — 347 KB gzipped is a
 * large download to spend on 8,908 features the visitor has not asked to see.
 * It is fetched the first time the layer is turned on, or on idle if a deep
 * link arrived with it already enabled.
 *
 * Returns `{ ensure }`; calling it repeatedly is safe and returns the same
 * promise.
 */
export function deferResidential(map, manifest, { onStart, onDone, eager = false } = {}) {
  let started = null;

  const ensure = () => {
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

  if (eager) {
    if ('requestIdleCallback' in window) requestIdleCallback(ensure, { timeout: 4000 });
    else setTimeout(ensure, 1200);
  }

  return { ensure };
}
