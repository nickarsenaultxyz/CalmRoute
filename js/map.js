/** MapLibre initialisation and basemap handling. */

import { BASEMAPS, DEFAULT_BASEMAP } from './config.js';

const RATIO = window.devicePixelRatio > 1.5 ? '@2x' : '';

function rasterSource(key) {
  const bm = BASEMAPS[key];
  if (!bm || !bm.tiles) return null;
  return {
    type: 'raster',
    tiles: bm.tiles.map((t) => t.replace('{ratio}', RATIO)),
    tileSize: 256,
    maxzoom: bm.maxzoom,
    attribution: bm.attribution,
  };
}

function baseStyle(key) {
  const src = rasterSource(key);
  return {
    version: 8,
    sources: src ? { basemap: src } : {},
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#f7f7f5' } },
      ...(src ? [{ id: 'basemap', type: 'raster', source: 'basemap' }] : []),
    ],
  };
}

/**
 * MapLibre 5 requires WebGL2. Fail with an explanation rather than a white
 * screen, and point at the raw data, which is open anyway.
 *
 * Do NOT use `maplibregl.supported()` here. That helper existed in v2/v3 and
 * was REMOVED by v5 — `typeof maplibregl.supported` is `undefined`, so the
 * obvious-looking guard `maplibregl.supported && maplibregl.supported()` is
 * always falsy and every visitor gets the fallback on a perfectly capable
 * browser. Verified against 5.24.0 in headless Chrome. Test the actual
 * capability instead.
 */
function hasWebGL2() {
  try {
    const canvas = document.createElement('canvas');
    return !!(canvas.getContext('webgl2')
      || canvas.getContext('experimental-webgl2'));
  } catch {
    return false;
  }
}

export function checkSupport() {
  if (window.maplibregl && hasWebGL2()) return true;
  document.getElementById('map').innerHTML = `
    <div class="fallback">
      <h1>This map needs WebGL2</h1>
      <p>Your browser or device cannot render it. The data is open and
         downloadable directly:</p>
      <ul>
        <li><a href="./data/network.geojson">network.geojson</a></li>
        <li><a href="./data/stats.json">stats.json</a></li>
      </ul>
    </div>`;
  document.getElementById('loading')?.setAttribute('hidden', '');
  return false;
}

export function createMap(manifest, initial = {}) {
  const map = new maplibregl.Map({
    container: 'map',
    style: baseStyle(initial.basemap || DEFAULT_BASEMAP),
    center: initial.center || manifest.center,
    zoom: initial.zoom ?? manifest.zoom,
    minZoom: 9,
    maxZoom: 19,
    keyboard: true,
    attributionControl: {
      compact: true,
      customAttribution:
        '<a href="https://www.openstreetmap.org/copyright" '
        + 'target="_blank" rel="noopener">Supplementary bike data © '
        + 'OpenStreetMap contributors (ODbL)</a>',
    },
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(
    new maplibregl.GeolocateControl({
      positionOptions: { enableHighAccuracy: true },
      trackUserLocation: true,
    }),
    'top-right',
  );
  map.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-right');

  installTileFallback(map);
  return map;
}

/**
 * CARTO is free-with-attribution and has no SLA. Swap to the Esri gray canvas
 * after repeated tile failures rather than leaving the user on a blank page.
 */
function installTileFallback(map) {
  let failures = 0;
  let swapped = false;
  map.on('error', (e) => {
    if (!e || !e.sourceId || e.sourceId !== 'basemap') return;
    if (swapped || ++failures < 6) return;
    swapped = true;
    console.warn('basemap tiles failing; falling back to Esri gray canvas');
    setBasemap(map, 'gray');
  });
}

/** Swap the basemap without touching the data layers. */
export function setBasemap(map, key) {
  const src = rasterSource(key);
  if (map.getLayer('basemap')) map.removeLayer('basemap');
  if (map.getSource('basemap')) map.removeSource('basemap');
  if (!src) return;
  map.addSource('basemap', src);
  // Insert directly above the background so every data layer stays on top.
  const first = map.getStyle().layers.find((l) => l.id !== 'bg');
  map.addLayer({ id: 'basemap', type: 'raster', source: 'basemap' },
    first ? first.id : undefined);
}
