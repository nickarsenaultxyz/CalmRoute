/**
 * Progressive data loading.
 *
 * The manifest drives every other fetch, so no path is hardcoded and a rebuild
 * can rename or version files freely.
 *
 * The files stay split so each layer can be filtered independently. The
 * complete rated network is now shown by default, so residential streets load
 * as soon as the primary facility layer is ready.
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
export const loadGraph = (m) => getJSON(m._url('graph'));
export const loadPlanned = (m) => getJSON(m._url('planned'));
export const loadCouncil = (m) => getJSON(m._url('council'));

/**
 * Fetch the residential layer at most once.
 *
 * It is fetched immediately for the default complete-network view. A shared
 * link that explicitly hides residential streets can still defer the request
 * until the layer is turned back on or routing needs its geometry.
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

  if (eager) ensure();

  return { ensure };
}
