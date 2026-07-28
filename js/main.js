/** Application entry point. */

import { LTS_ORDER_LEGEND } from './config.js';
import {
  deferResidential, loadContext, loadManifest, loadMethodology, loadNetwork,
  loadStats,
} from './data.js';
import {
  addLayers, addSources, hitLayers, setLtsVisible, setSourceVisible,
} from './layers.js';
import { announce, easeTo, isCoarsePointer } from './lib/a11y.js';
import { debounce, onPopState, read, write } from './lib/urlstate.js';
import { checkSupport, createMap, setBasemap } from './map.js';
import { Panel } from './panel.js';
import * as detail from './views/detail.js';
import * as legend from './views/legend.js';

const els = {
  loading: document.getElementById('loading'),
  loadingText: document.getElementById('loading-text'),
  hover: document.getElementById('hover-card'),
};

/** Exposed for debugging and automated checks. Read-only in spirit — nothing in
 *  the app reads it back. */
const app = window.__lexbike = {
  state: read(),
  stats: null,
  methodology: null,
  aadtYear: null,
  featuresById: new Map(),
  selected: null,
  hovered: null,
};

function setLoading(text) {
  if (!text) {
    els.loading.classList.add('fade');
    setTimeout(() => els.loading.setAttribute('hidden', ''), 300);
    return;
  }
  els.loading.removeAttribute('hidden');
  els.loading.classList.remove('fade');
  els.loadingText.textContent = text;
}

function fail(message, err) {
  console.error(message, err);
  setLoading(null);
  app.panel?.show({
    title: 'Could not load the map',
    root: true,
    html: `<p class="note">${message}</p>
           <p class="tech">The data is open and can be downloaded directly:
             <a href="./data/network.geojson">network.geojson</a>.</p>`,
  });
}

/* ------------------------------------------------------------------ views */

function showLegend({ push = false } = {}) {
  app.state.view = 'legend';
  app.state.selected = null;
  clearSelection();
  const body = app.panel.show({
    title: 'Lexington Bike Stress',
    root: true,
    html: legend.render(app.stats, app.state),
  });
  legend.mount(body, {
    onLts: (lts, on) => {
      on ? app.state.lts.add(lts) : app.state.lts.delete(lts);
      setLtsVisible(app.map, lts, on);
      syncUrl();
    },
    onResidential: (on) => {
      app.state.residential = on;
      setSourceVisible(app.map, 'residential', on);
      syncUrl();
    },
  });
  write(app.state, { push });
}

function showDetail(feature, { push = true } = {}) {
  const props = feature.properties || {};
  app.state.view = 'segment';
  app.state.selected = feature.id;
  select(feature);

  app.panel.show({
    title: props.nm || 'Unnamed street',
    html: detail.render(props, { stats: app.stats, aadtYear: app.aadtYear }),
  });
  write(app.state, { push });
  announce(`Selected ${props.nm || 'unnamed street'}, rated ${
    (app.stats && props.lts != null) ? detailRatingWord(props.lts) : 'unknown'}.`);
}

const detailRatingWord = (lts) =>
  ({ 0: 'bikes not permitted', 1: 'relaxed', 2: 'comfortable for most adults',
     3: 'busy', 4: 'stressful' }[lts] || 'unknown');

/* -------------------------------------------------------------- selection */

function select(feature) {
  clearSelection();
  app.selected = { source: feature.source, id: feature.id };
  app.map.setFeatureState(app.selected, { selected: true });
}

function clearSelection() {
  if (!app.selected) return;
  app.map.setFeatureState(app.selected, { selected: false });
  app.selected = null;
}

/* ------------------------------------------------------------ interaction */

function installInteraction(map) {
  const layers = hitLayers(map);
  if (!layers.length) return;

  map.on('mousemove', layers, (ev) => {
    const f = ev.features?.[0];
    if (!f) return;
    if (app.hovered) map.setFeatureState(app.hovered, { hover: false });
    app.hovered = { source: f.source, id: f.id };
    map.setFeatureState(app.hovered, { hover: true });
    map.getCanvas().style.cursor = 'pointer';

    els.hover.innerHTML = detail.hoverHtml(f.properties || {});
    els.hover.hidden = false;
    const pad = 14;
    els.hover.style.left = `${Math.min(ev.point.x + pad, window.innerWidth - 250)}px`;
    els.hover.style.top = `${ev.point.y + pad}px`;
  });

  map.on('mouseleave', layers, () => {
    if (app.hovered) map.setFeatureState(app.hovered, { hover: false });
    app.hovered = null;
    map.getCanvas().style.cursor = '';
    els.hover.hidden = true;
  });

  map.on('click', (ev) => {
    // A 2.6px residential line is unhittable with a fingertip at the default
    // tolerance, so coarse pointers get a 24px target.
    const pad = isCoarsePointer() ? 12 : 4;
    const box = [
      [ev.point.x - pad, ev.point.y - pad],
      [ev.point.x + pad, ev.point.y + pad],
    ];
    const hits = map.queryRenderedFeatures(box, { layers });
    if (!hits.length) {
      showLegend({ push: true });
      return;
    }
    // Prefer the best facility when several overlap under one finger.
    hits.sort((a, b) => (a.properties.lts ?? 9) - (b.properties.lts ?? 9));
    showDetail(hits[0]);
    if (app.panel.isSheet) app.panel.focusBody();
  });
}

/* -------------------------------------------------------------------- url */

const syncUrl = debounce(() => {
  const c = app.map.getCenter();
  app.state.center = [c.lng, c.lat];
  app.state.zoom = app.map.getZoom();
  write(app.state);
}, 300);

/* ------------------------------------------------------------------- boot */

async function boot() {
  if (!checkSupport()) return;

  app.panel = new Panel({ onBack: () => history.back() });

  let manifest;
  try {
    manifest = await loadManifest();
  } catch (err) {
    fail('The map data could not be found. If you are running this locally, '
       + 'serve the folder over HTTP (<code>make serve</code>) rather than '
       + 'opening the file directly.', err);
    return;
  }

  const map = createMap(manifest, app.state);
  app.map = map;

  // Numbers before geometry: ~18 KB gets every figure and the legend on screen
  // while the network is still downloading.
  loadStats(manifest).then((s) => {
    app.stats = s;
    app.aadtYear = s?.data_sources?.aadt_count_years?.median ?? null;
    if (app.state.view !== 'segment') showLegend();
  }).catch((err) => console.warn('stats unavailable', err));

  loadMethodology(manifest)
    .then((m) => { app.methodology = m; })
    .catch(() => {});

  map.on('load', async () => {
    addSources(map, manifest);
    addLayers(map);

    for (const lts of LTS_ORDER_LEGEND) {
      if (!app.state.lts.has(lts)) setLtsVisible(map, lts, false);
    }
    if (!app.state.residential) setSourceVisible(map, 'residential', false);

    installInteraction(map);

    // Facilities first -- 57 KB, and the actual answer to "can I ride here".
    try {
      setLoading('Loading the bike network…');
      const fc = await loadNetwork(manifest);
      map.getSource('network').setData(fc);
      for (const f of fc.features) app.featuresById.set(f.id, f);
      announce('Bike network loaded.');
    } catch (err) {
      fail('The bike network layer failed to load.', err);
      return;
    }

    // Busy roads next. Not deferred to idle like the residential bulk: without
    // them the map reads as a handful of disconnected trails floating in space,
    // which overstates how good the network is.
    setLoading('Adding busy roads…');
    loadContext(manifest)
      .then((fc) => {
        map.getSource('context').setData(fc);
        for (const f of fc.features) app.featuresById.set(f.id, f);
        setLoading(null);
      })
      .catch((err) => { console.error('context layer failed', err); setLoading(null); });

    deferResidential(map, manifest, {
      onStart: () => setLoading('Adding neighbourhood streets…'),
      onDone: (fc) => {
        setLoading(null);
        if (fc) for (const f of fc.features) app.featuresById.set(f.id, f);
        if (app.state.selected != null && !app.selected) restoreSelection();
      },
    });

    if (app.state.selected != null) restoreSelection();

    map.on('moveend', syncUrl);
  });

  onPopState((next) => {
    app.state = { ...app.state, ...next };
    if (next.view === 'segment' && next.selected != null) restoreSelection(false);
    else showLegend({ push: false });
  });
}

/** Re-open a deep-linked segment once its layer has arrived. */
function restoreSelection(push = false) {
  const f = app.featuresById.get(app.state.selected);
  if (!f) return;
  // Mirrors the export-side split in lexbike/pipeline.py: facilities are in
  // `network`, everything else divides on low-stress vs not.
  const p = f.properties;
  const src = p.fac ? 'network' : (p.lts <= 2 ? 'residential' : 'context');
  showDetail({ ...f, source: src }, { push });
  if (f.geometry?.coordinates?.length) {
    const c = f.geometry.coordinates[Math.floor(f.geometry.coordinates.length / 2)];
    easeTo(app.map, { center: c, zoom: Math.max(app.map.getZoom(), 15) });
  }
}

boot();
