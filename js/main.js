/** Application entry point. */

import {
  DEFAULT_ROUTE_LEVEL, LTS, LTS_ORDER_LEGEND, QUIETEST_ROUTE_LEVEL, ROUTE_LEVELS,
} from './config.js';
import {
  deferResidential, loadContext, loadCouncil, loadGraph, loadManifest,
  loadMethodology, loadNetwork, loadStats,
} from './data.js?v=20260729-live-data-refresh';
import {
  addConnectorLayer, addLayers, addRouteLayers, addSources, hitLayers, setLtsVisible,
  setRoute, setRouteAccess, setRouteEndpoints, setSourceVisible,
} from './layers.js?v=20260729-route-colors';
import { clipToEnd } from './lib/graph.js';
import { announce, easeTo, isCoarsePointer } from './lib/a11y.js';
import { debounce, onPopState, read, write } from './lib/urlstate.js';
import { checkSupport, createMap, setBasemap } from './map.js?v=20260731-commonwealth-lts1';
import { Panel } from './panel.js';
import * as browse from './views/browse.js';
import * as detail from './views/detail.js?v=20260731-commonwealth-lts1';
import * as legend from './views/legend.js';
import * as methodology from './views/methodology.js?v=20260731-commonwealth-lts1';
import * as routeView from './views/route.js?v=20260731-route-geometry-loading';
import * as settings from './views/settings.js';
import * as share from './views/share.js';

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
  graph: null,
  contextPromise: null,
  route: {
    from: null, to: null, level: DEFAULT_ROUTE_LEVEL,
    picking: 'from', result: null,
  },
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
      if (on) app.residential?.ensure();
      syncUrl();
      showLegend();   // mileages describe what is drawn, so they all change
    },
    onNav: (view) => openView(view, { push: true }),
  });
  write(app.state, { push });
}

/* --- secondary views ---------------------------------------------------- */

function openView(view, { push = true } = {}) {
  if (view === 'browse') return showBrowse({ push });
  if (view === 'share') return showShare({ push });
  if (view === 'style') return showSettings({ push });
  if (view === 'methodology') return showMethodology({ push });
  if (view === 'route') return showRoute({ push });
  return showLegend({ push });
}

function showBrowse({ push = true } = {}) {
  app.state.view = 'browse';

  const draw = (loadedAll) => {
    const index = browse.buildIndex(app.featuresById);
    const body = app.panel.show({
      title: 'Browse streets',
      html: browse.render(index, { loadedAll }),
    });
    const ctl = browse.mount(body, index, {
      onPick: (ids) => {
        const f = app.featuresById.get(ids[0]);
        if (!f) return;
        app.state.selected = f.id;
        restoreSelection(true);
      },
    });
    // Focus the search box: this view exists for people not using a mouse.
    ctl.focus();
    announce(`Browsing ${index.length} streets by name.`);
    return index;
  };

  draw(app.residentialIndexed === true);

  // The non-map path has to be complete or it is a second-class experience, so
  // pull in the quiet-street names even though the layer stays hidden. The data
  // is only fetched once; the layer's visibility is untouched.
  if (!app.residentialIndexed && app.residential) {
    app.residential.ensure().then(() => {
      app.residentialIndexed = true;
      if (app.state.view === 'browse') draw(true);
    });
  }

  write(app.state, { push });
}

function showShare({ push = true } = {}) {
  app.state.view = 'share';
  const seg = app.state.selected != null
    ? app.featuresById.get(app.state.selected)?.properties
    : null;
  const segment = seg ? {
    nm: seg.nm,
    rating: LTS[seg.lts]?.short || detailRatingWord(seg.lts),
    detail: LTS[seg.lts]?.detail || '',
    district: seg.cd ?? null,
  } : null;

  const ctx = { stats: app.stats, segment, council: app.council, announce };
  const body = app.panel.show({
    title: segment ? 'Share this street' : 'Share this map',
    html: share.render(app.stats, ctx),
  });
  share.mount(body, ctx);
  app.panel.focusBody();
  write(app.state, { push });
}

function showMethodology({ push = true } = {}) {
  app.state.view = 'methodology';
  app.panel.show({
    title: 'What the ratings mean',
    html: methodology.render(app.methodology, app.stats),
  });
  app.panel.focusBody();
  write(app.state, { push });
}

function showRoute({ push = true } = {}) {
  app.state.view = 'route';
  const body = app.panel.show({
    title: 'Plan a route',
    html: routeView.render(app.route, app.stats),
  });
  routeView.mount(body, {
    onPick: (which) => {
      app.route.picking = app.route.picking === which ? null : which;
      announce(app.route.picking
        ? `Tap the map to set the ${which === 'from' ? 'start' : 'destination'}.`
        : 'Cancelled.');
      showRoute({ push: false });
    },
    onClear: (which) => { app.route[which] = null; recomputeRoute(); },
    onClearBoth: () => {
      app.route.from = app.route.to = null;
      app.route.result = null;
      app.route.picking = 'from';
      drawRoute(null);
      showRoute({ push: false });
    },
    onSwap: () => {
      const t = app.route.from;
      app.route.from = app.route.to;
      app.route.to = t;
      recomputeRoute();
    },
    onLevel: (level) => {
      app.route.level = Math.max(0, Math.min(QUIETEST_ROUTE_LEVEL, level));
      announce(`Route preference: ${ROUTE_LEVELS[app.route.level].label}.`);
      recomputeRoute();
    },
    // The offer attached to a route that turned out to be busy: switch to the
    // setting that found the quieter one, so the slider agrees with the map.
    onQuieter: () => {
      const alt = app.route.result?.quieter;
      if (!alt) return;
      app.route.level = alt.level;
      recomputeRoute();
    },
    onAccept: () => {
      // Draw the compromised route the rider explicitly asked to see.
      if (app.route.fallbackResult) {
        app.route.result = { kind: 'ok', ...app.route.fallbackResult };
        if (!drawRoute(app.route.fallbackResult)) {
          app.route.result = { kind: 'error' };
        }
        showRoute({ push: false });
      }
    },
    onNav: (view) => openView(view, { push: true }),
  }, app.route);
  write(app.state, { push });
}

function showSettings({ push = true } = {}) {
  app.state.view = 'style';
  const body = app.panel.show({
    title: 'Map style',
    html: settings.render(app.state.basemap),
  });
  settings.mount(body, {
    current: app.state.basemap,
    onPick: (key) => {
      app.state.basemap = key;
      setBasemap(app.map, key);
      syncUrl();
      announce(`Map style set to ${key}.`);
    },
  });
  app.panel.focusBody();
  write(app.state, { push });
}

function showDetail(feature, { push = true } = {}) {
  const props = feature.properties || {};
  app.state.view = 'segment';
  app.state.selected = feature.id;
  select(feature);

  const body = app.panel.show({
    title: props.nm || 'Unnamed street',
    html: detail.render(props, {
      stats: app.stats, aadtYear: app.aadtYear, council: app.council,
    }),
  });
  body.querySelectorAll('[data-nav]').forEach((btn) => {
    btn.addEventListener('click', () => openView(btn.dataset.nav, { push: true }));
  });
  write(app.state, { push });
  announce(`Selected ${props.nm || 'unnamed street'}, rated ${
    (app.stats && props.lts != null) ? detailRatingWord(props.lts) : 'unknown'}.`);
}

const detailRatingWord = (lts) =>
  ({ 0: 'bikes not permitted', 1: 'relaxed', 2: 'comfortable for most adults',
     3: 'busy', 4: 'stressful' }[lts] || 'unknown');

/* ---------------------------------------------------------------- routing */

/** Load busy-road geometry once and register it for route drawing.
 *
 * The graph can search before MapLibre has finished fetching this layer, but a
 * route is not drawable until the corresponding feature geometries are also in
 * `featuresById`. Keeping one shared promise lets initial map loading and the
 * router wait on the same work instead of racing each other.
 */
function ensureContext() {
  if (app.contextPromise) return app.contextPromise;
  setLoading('Adding busy roads…');
  app.contextPromise = loadContext(app.manifest)
    .then((fc) => {
      app.map.getSource('context')?.setData(fc);
      for (const f of fc.features) app.featuresById.set(f.id, f);
      setLoading(null);
      return fc;
    })
    .catch((err) => {
      console.error('context layer failed', err);
      setLoading(null);
      throw err;
    });
  return app.contextPromise;
}

/** Load the graph and build the CSR structure once, on first use. */
async function ensureGraph() {
  if (app.graph) return app.graph;
  if (!app.graphPromise) {
    // The route is drawn from geometry already in memory, keyed by feature id,
    // so every layer the route can traverse must be loaded -- not necessarily
    // visible. Without this the quiet-street portions of a route have no
    // geometry and the line renders as disconnected fragments.
    app.graphPromise = Promise.all([
      loadGraph(app.manifest),
      ensureContext(),
      app.residential ? app.residential.ensure() : null,
    ]).then(async ([raw, context, residential]) => {
      if (!context || !residential) {
        throw new Error('A map geometry layer required for routing did not load.');
      }
      const { buildGraph, components } = await import('./lib/graph.js');
      app.graph = buildGraph(raw);
      // Island labels answer "is a comfortable route even possible" before a
      // search runs, so the failure message can name both islands.
      app.lowStressLabels = components(app.graph, 2);
      return app.graph;
    });
  }
  return app.graphPromise;
}

async function recomputeRoute() {
  const { from, to } = app.route;
  if (!from || !to) {
    app.route.result = null;
    drawRoute(null);
    showRoute({ push: false });
    return;
  }

  app.route.result = { kind: 'pending' };
  showRoute({ push: false });

  let g;
  try {
    g = await ensureGraph();
  } catch (err) {
    console.error('route geometry unavailable', err);
    app.route.result = { kind: 'error' };
    drawRoute(null);
    showRoute({ push: false });
    return;
  }
  const { snapToNetwork } = await import('./lib/graph.js');
  const dj = await import('./lib/dijkstra.js');

  const geometryOf = (id) => app.featuresById.get(id)?.geometry?.coordinates;
  const snapA = snapToNetwork(g, geometryOf, from.lng, from.lat);
  const snapB = snapToNetwork(g, geometryOf, to.lng, to.lat);
  if (!snapA || !snapB) {
    app.route.result = { kind: 'none' };
    drawRoute(null);
    showRoute({ push: false });
    return;
  }
  app.route.snapA = snapA;
  app.route.snapB = snapB;

  const level = app.route.level;
  const primary = routeBetweenSnaps(g, dj, snapA, snapB,
    ROUTE_LEVELS[level].key);
  const shortest = routeBetweenSnaps(g, dj, snapA, snapB, 'shortest');

  if (!primary) {
    // Distinguish "no comfortable route" from "not connected at all". The
    // first is the finding; the second is a data gap. Only the strictest
    // setting is a hard filter, so only it can refuse a connected pair.
    const fallback = level === QUIETEST_ROUTE_LEVEL
      ? routeBetweenSnaps(g, dj, snapA, snapB, 'quiet') : null;
    app.route.fallbackResult = fallback
      ? { ...fallback, detour: shortest ? fallback.miles / shortest.miles : null }
      : null;
    const sameIsland = app.lowStressLabels
      && app.lowStressLabels[snapA.u] === app.lowStressLabels[snapB.u];
    app.route.result = (fallback || !sameIsland)
      ? {
          kind: 'blocked',
          islandA: islandOf(snapA.u),
          islandB: islandOf(snapB.u),
          fallback: app.route.fallbackResult,
        }
      : { kind: 'none' };
    drawRoute(null);
    showRoute({ push: false });
    return;
  }

  app.route.result = {
    kind: 'ok',
    ...primary,
    level,
    detour: shortest ? primary.miles / shortest.miles : null,
    quieter: quieterThan(g, dj, snapA, snapB, primary, level),
  };
  if (!drawRoute(primary)) {
    app.route.result = { kind: 'error' };
    showRoute({ push: false });
    return;
  }
  showRoute({ push: false });
  announce(primary.stressMiles > 0.01
    ? `Route found: ${primary.miles.toFixed(1)} miles, including ${
      primary.stressMiles.toFixed(1)} on busy roads.`
    : `Route found: ${primary.miles.toFixed(1)} miles, comfortable the whole way.`);
}

/**
 * The least-stressful alternative worth offering, or null.
 *
 * A note saying "0.4 miles of this is busy" is only half an answer; the rider's
 * next question is what avoiding it would cost. So the quieter settings are
 * searched too, and the offer carries the real distance rather than being a
 * button that might do nothing. Scanning forward rather than stopping at the
 * next notch matters because one step quieter often finds the same road — the
 * penalty has to clear the detour before the route changes at all.
 *
 * Costs up to two extra searches, each four Dijkstras of 0.2-0.8 ms, and only
 * runs when the route is actually stressful.
 */
function quieterThan(g, dj, snapA, snapB, primary, level) {
  if (primary.stressMiles <= 0.01) return null;

  for (let next = level + 1; next <= QUIETEST_ROUTE_LEVEL; next++) {
    const alt = routeBetweenSnaps(g, dj, snapA, snapB, ROUTE_LEVELS[next].key);
    if (!alt) continue;
    // Either measure improving is an improvement. Requiring less total stress
    // would reject the most valuable trade the quieter settings make -- swapping
    // an arterial for a longer run of busy collector, which lowers `severeMiles`
    // while raising `stressMiles`. Ties are not offers: an unchanged route under
    // a different label reads as a broken button.
    const lessArterial = alt.severeMiles < primary.severeMiles - 0.01;
    const lessStress = alt.stressMiles < primary.stressMiles - 0.01;
    if (lessArterial || lessStress) return { level: next, ...alt };
  }
  return null;
}

/**
 * Route between two points snapped onto edges rather than onto nodes.
 *
 * The snap point usually sits partway along an edge, so the search has to
 * consider leaving via either end of the start edge and arriving via either end
 * of the destination edge -- four pairings -- charging the partial edge at each
 * end. Picking the nearest node instead is what left the drawn route stopping
 * short of the pin.
 *
 * Four searches still cost under 5 ms on this graph.
 */
function routeBetweenSnaps(g, dj, snapA, snapB, mode) {
  // Both points on the same edge: no search needed, just the piece between.
  if (snapA.edge === snapB.edge) {
    const frac = Math.abs(snapB.fraction - snapA.fraction);
    const miles = g.eMi[snapA.edge] * frac;
    const lts = g.eLts[snapA.edge];
    // Ask the mode itself rather than re-deriving which ratings it allows;
    // hardcoding that list here is how it drifts from the penalty table.
    if (!dj.passable(g, snapA.edge, mode)) return null;
    return {
      edges: [snapA.edge], featureIds: [g.eId[snapA.edge]],
      miles, stressMiles: lts > 2 ? miles : 0,
      severeMiles: lts === 4 ? miles : 0, worstLts: lts,
      partial: { sameEdge: true },
    };
  }

  const ends = (snap) => [
    { node: snap.u, frac: snap.fraction },        // travelling back to u
    { node: snap.v, frac: 1 - snap.fraction },    // travelling on to v
  ];

  let best = null;
  for (const a of ends(snapA)) {
    for (const b of ends(snapB)) {
      const r = dj.route(g, a.node, b.node, mode);
      if (!r) continue;
      // Charge the partial edge at each end so the comparison is fair.
      const addA = g.eMi[snapA.edge] * a.frac;
      const addB = g.eMi[snapB.edge] * b.frac;
      const total = r.miles + addA + addB;
      if (!best || total < best.total) best = { total, r, a, b, addA, addB };
    }
  }
  if (!best) return null;

  const { r, a, b, addA, addB } = best;
  const ltsA = g.eLts[snapA.edge];
  const ltsB = g.eLts[snapB.edge];
  return {
    edges: r.edges,
    featureIds: r.featureIds,
    miles: best.total,
    stressMiles: r.stressMiles + (ltsA > 2 ? addA : 0) + (ltsB > 2 ? addB : 0),
    severeMiles: r.severeMiles + (ltsA === 4 ? addA : 0) + (ltsB === 4 ? addB : 0),
    worstLts: Math.max(r.worstLts, ltsA, ltsB),
    partial: { startNode: a.node, endNode: b.node },
  };
}

/** Island number for a graph node, read off the segments it belongs to. */
function islandOf(node) {
  const g = app.graph;
  if (!g) return null;
  for (let k = g.head[node]; k < g.head[node + 1]; k++) {
    const f = app.featuresById.get(g.eId[g.via[k]]);
    if (f && f.properties.isl != null) return f.properties.isl;
  }
  return null;
}

/**
 * Draw the route from geometry already in memory, keyed by feature id.
 *
 * The two partial edges at the ends are clipped at the snap points, and a short
 * dashed stub covers whatever is left between the pin and the network -- that
 * last bit is real (you do have to get to the street somehow) and pretending
 * otherwise would draw a route that starts in a field.
 */
function drawRoute(result) {
  if (!result) {
    setRoute(app.map, null);
    setRouteAccess(app.map, null);
    return true;
  }
  const features = [];
  const push = (coords, lts) => {
    if (coords && coords.length > 1) {
      features.push({
        type: 'Feature',
        properties: { lts },
        geometry: { type: 'LineString', coordinates: coords },
      });
    }
  };

  const { snapA, snapB } = app.route;
  const partial = result.partial || {};

  if (partial.sameEdge && snapA && snapB) {
    const lo = Math.min(snapA.alongDeg, snapB.alongDeg);
    const hi = Math.max(snapA.alongDeg, snapB.alongDeg);
    const first = snapA.alongDeg <= snapB.alongDeg ? snapA : snapB;
    const last = first === snapA ? snapB : snapA;
    push([[first.x, first.y], ...first.coords.slice(
      first.vertexIndex + 1, last.vertexIndex + 1), [last.x, last.y]],
      app.graph.eLts[snapA.edge]);
  } else {
    if (snapA) {
      push(clipToEnd(snapA, partial.startNode === snapA.v),
           app.graph.eLts[snapA.edge]);
    }
    const missingIds = [];
    for (const id of result.featureIds) {
      const f = app.featuresById.get(id);
      if (f) push(f.geometry.coordinates, f.properties.lts);
      else missingIds.push(id);
    }
    if (missingIds.length) {
      console.error('route feature geometry missing', missingIds);
      setRoute(app.map, null);
      setRouteAccess(app.map, null);
      return false;
    }
    if (snapB) {
      push(clipToEnd(snapB, partial.endNode === snapB.v),
           app.graph.eLts[snapB.edge]);
    }
  }

  setRoute(app.map, { type: 'FeatureCollection', features });

  // The unavoidable remainder: pin to network.
  const stubs = [];
  for (const [pin, snap] of [[app.route.from, snapA], [app.route.to, snapB]]) {
    if (!pin || !snap) continue;
    stubs.push({
      type: 'Feature', properties: {},
      geometry: { type: 'LineString',
                  coordinates: [[pin.lng, pin.lat], [snap.x, snap.y]] },
    });
  }
  setRouteAccess(app.map, { type: 'FeatureCollection', features: stubs });
  return true;
}

function setRoutePoint(which, lngLat) {
  app.route[which] = { lng: lngLat.lng, lat: lngLat.lat };
  app.route.picking = which === 'from' && !app.route.to ? 'to' : null;
  setRouteEndpoints(app.map, [
    app.route.from && { ...app.route.from, role: 'from' },
    app.route.to && { ...app.route.to, role: 'to' },
  ]);
  recomputeRoute();
}

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
    // While picking route endpoints the map click means "here", not "what is
    // this street".
    if (app.state.view === 'route' && app.route.picking) {
      setRoutePoint(app.route.picking, ev.lngLat);
      return;
    }
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
  // Tells the watchdog in index.html that the module graph linked and ran.
  window.__lexbikeBooted = true;
  if (!checkSupport()) return;

  app.panel = new Panel({ onBack: () => history.back() });

  // The skip link promises "browse streets as a list", so honour that rather
  // than just moving focus into whatever the panel happens to be showing.
  document.querySelector('.skip-link')?.addEventListener('click', (ev) => {
    ev.preventDefault();
    showBrowse({ push: true });
  });

  // Escape returns to the overview from any secondary view, and clears a
  // selection -- the conventional way out of a detail pane.
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    if (app.state.view && app.state.view !== 'legend') {
      showLegend({ push: true });
      app.panel.focusBody();
    }
  });

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
  app.manifest = manifest;

  // Capture what the URL asked for before anything overwrites it: showLegend()
  // sets state.view = 'legend', so by the time the data has loaded the original
  // request is gone.
  const requestedView = app.state.view;

  // Numbers before geometry: ~18 KB gets every figure and the legend on screen
  // while the network is still downloading.
  loadStats(manifest).then((s) => {
    app.stats = s;
    app.aadtYear = s?.data_sources?.aadt_count_years?.median ?? null;
    // Render the overview immediately so the panel is never blank; the URL's
    // own view is restored below, once the data it may depend on has arrived.
    if (app.state.view !== 'segment') showLegend();
  }).catch((err) => console.warn('stats unavailable', err));

  loadMethodology(manifest)
    .then((m) => { app.methodology = m; })
    .catch(() => {});

  // Council roster: small, and needed the moment someone opens the share view.
  loadCouncil(manifest)
    .then((c) => { app.council = c; })
    .catch((err) => console.warn('council roster unavailable', err));

  map.on('load', async () => {
    addSources(map, manifest);
    addLayers(map);
    addConnectorLayer(map);
    addRouteLayers(map);

    for (const lts of LTS_ORDER_LEGEND) {
      if (!app.state.lts.has(lts)) setLtsVisible(map, lts, false);
    }
    setSourceVisible(map, 'residential', app.state.residential);

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
    // Start this eagerly, but keep the promise so routing can wait for the
    // geometry instead of drawing a graph result with context-road gaps.
    ensureContext().catch(() => {});

    // Off by default, so this is not prefetched -- it is fetched the first time
    // the layer is switched on (or immediately if a deep link arrived with it
    // already enabled).
    app.residential = deferResidential(map, manifest, {
      eager: app.state.residential,
      onStart: () => setLoading('Adding neighbourhood streets…'),
      onDone: (fc) => {
        setLoading(null);
        if (fc) for (const f of fc.features) app.featuresById.set(f.id, f);
        if (app.state.selected != null && !app.selected) restoreSelection();
      },
    });

    // Restore whatever the URL asked for. This runs after the network layer
    // has loaded because several views read from it -- a shared link to
    // ?view=browse or ?view=methodology used to fall back to the overview.
    if (app.state.selected != null) {
      restoreSelection();
    } else if (requestedView && !['legend', 'segment'].includes(requestedView)) {
      openView(requestedView, { push: false });
    }

    map.on('moveend', syncUrl);
  });

  onPopState((next) => {
    app.state = { ...app.state, ...next };
    if (next.view === 'segment' && next.selected != null) restoreSelection(false);
    else openView(next.view, { push: false });
  });
}

/**
 * Re-open a deep-linked segment once its layer has arrived.
 *
 * A shared link can point at a quiet street, and quiet streets are no longer
 * fetched by default. If the id is unknown, pull that layer in once and retry
 * rather than silently doing nothing — a shared link that opens a blank map is
 * worse than a slow one.
 */
function restoreSelection(push = false) {
  const f = app.featuresById.get(app.state.selected);
  if (!f) {
    if (app.residential && !restoreSelection._retried) {
      restoreSelection._retried = true;
      app.residential.ensure().then(() => restoreSelection(push));
    }
    return;
  }
  // Mirrors the export-side split in lexbike/pipeline.py: facilities are in
  // `network`, everything else divides on low-stress vs not.
  const p = f.properties;
  const src = p.fac ? 'network' : (p.lts <= 2 ? 'residential' : 'context');
  // Reveal the layer the shared segment lives on, or the highlight lands on
  // something invisible.
  if (src === 'residential' && !app.state.residential) {
    app.state.residential = true;
    setSourceVisible(app.map, 'residential', true);
  }
  showDetail({ ...f, source: src }, { push });
  if (f.geometry?.coordinates?.length) {
    const c = f.geometry.coordinates[Math.floor(f.geometry.coordinates.length / 2)];
    easeTo(app.map, { center: c, zoom: Math.max(app.map.getZoom(), 15) });
  }
}

/**
 * Never leave the spinner running.
 *
 * A throw anywhere in boot() used to leave the loading chip spinning forever
 * with nothing in the console the reader can see. The commonest cause is a
 * stale module: browsers cache ES modules hard, so after a deploy a reader can
 * hold an old layers.js against a new main.js and the missing export takes the
 * whole boot down.
 */
boot().catch((err) => {
  console.error('boot failed', err);
  setLoading(null);
  const stale = /is not a function|has already been declared|Failed to fetch/i
    .test(String(err && err.message));
  app.panel?.show({
    title: 'The map failed to start',
    root: true,
    html: `<p class="note">${stale
      ? 'This usually means your browser is holding an old copy of the page. '
        + 'A hard refresh should fix it.'
      : 'Something went wrong while setting up the map.'}</p>
      <div class="share-actions">
        <button class="btn primary" onclick="location.reload(true)">Reload</button>
      </div>
      <p class="tech">${escapeHtmlLite(String(err && err.message || err))}</p>
      <p class="tech">The data is open either way:
        <a href="./data/network.geojson">network.geojson</a>.</p>`,
  });
});

function escapeHtmlLite(s) {
  return s.replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
