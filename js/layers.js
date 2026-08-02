/** Data layer construction and visibility. */

import {
  FAC_CONNECTOR, LAYERS, LTS, SOURCES,
} from './config.js?v=20260802-signal-orange';

export const layerId = (e) => `${e.src}-lts${e.lts}`;

// Keep the rest of the network as geographic context while a route is shown,
// without letting it compete with the route itself.
const ROUTE_CONTEXT_OPACITY = 0.18;

// Campus walkways are secondary bicycle links, not purpose-built bike
// infrastructure. Keep them visible and selectable while letting bike lanes
// and shared-use paths retain the stronger visual hierarchy.
const CAMPUS_WALKWAY_WIDTH_SCALE = 0.45;

// A selected route keeps its stress colours, while a small width change shows
// whether each leg has dedicated/shared bicycle infrastructure. The
// difference is deliberately modest: ordinary streets remain easy to follow.
const ROUTE_FACILITY_WIDTH = 6;
const ROUTE_STREET_WIDTH = 5;
const ROUTE_FACILITY_CASING_WIDTH = 12;
const ROUTE_STREET_CASING_WIDTH = 10.5;

const casingOpacity = (focused = false) => [
  'interpolate', ['linear'], ['zoom'],
  11, 0,
  13, focused ? ROUTE_CONTEXT_OPACITY : 1,
];

const connectorOpacity = (focused = false) => [
  'interpolate', ['linear'], ['zoom'],
  13, 0,
  15, focused ? 0.5 * ROUTE_CONTEXT_OPACITY : 0.5,
];

/**
 * Width expression.
 *
 * The non-obvious constraint: MapLibre only accepts `["zoom"]` as the direct
 * input to a TOP-LEVEL `step`/`interpolate`. Wrapping the interpolate in
 * arithmetic -- `['*', ['interpolate', ...], boost]` -- throws at addLayer. The
 * hover/select multiplier therefore has to live inside each output stop.
 */
function widthExpr(base) {
  const boost = ['case',
    ['boolean', ['feature-state', 'selected'], false], 2.2,
    ['boolean', ['feature-state', 'hover'], false], 1.7,
    1];
  const facilityScale = ['case',
    ['==', ['get', 'osm_role'], 'campus_path'],
    CAMPUS_WALKWAY_WIDTH_SCALE,
    1];
  return ['interpolate', ['exponential', 1.4], ['zoom'],
    10, ['*', base * 0.40, boost, facilityScale],
    12, ['*', base * 0.70, boost, facilityScale],
    14, ['*', base * 1.00, boost, facilityScale],
    16, ['*', base * 1.75, boost, facilityScale],
    18, ['*', base * 3.00, boost, facilityScale]];
}

export function addSources(map, manifest) {
  for (const src of SOURCES) {
    if (map.getSource(src)) continue;
    map.addSource(src, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
      // Deliberately NO `promoteId`. It promotes `properties.id`, and the
      // exporter writes `id` at the GeoJSON top level (which is where the spec
      // puts a feature id). Setting promoteId:'id' therefore looked up a
      // property that does not exist, leaving every feature with an undefined
      // id -- so feature-state hover/selection silently did nothing and `?sel=`
      // deep links never recorded anything. MapLibre already honours a
      // top-level numeric id for GeoJSON sources.
      // A wider tile buffer stops 5px lines seaming at tile edges.
      buffer: 64,
      tolerance: 0.375,
    });
  }
}

export function addLayers(map) {
  for (const entry of LAYERS) {
    const style = LTS[entry.lts];
    const id = layerId(entry);
    const base = style.width * (entry.scale ?? 1);
    // Connectors are short synthetic links from a trail to the street it runs
    // beside. Painted in the LTS 1 layer they read as real bike infrastructure
    // -- 1,483 perpendicular green stubs, 49 ft median, that nobody built.
    const filter = ['all',
      ['==', ['get', 'lts'], entry.lts],
      ['!=', ['coalesce', ['get', 'fac'], 0], FAC_CONNECTOR]];

    if (entry.casing) {
      map.addLayer({
        id: `${id}-casing`,
        type: 'line',
        source: entry.src,
        filter,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          // A cool light casing separates status colours from the light tiles.
          'line-color': 'rgba(248,250,252,0.90)',
          'line-width': widthExpr(base + 2.4),
          // A halo is noise when the city fits on screen.
          'line-opacity': casingOpacity(),
        },
      });
    }

    map.addLayer({
      id,
      type: 'line',
      source: entry.src,
      filter,
      layout: {
        'line-cap': 'round',
        'line-join': 'round',
      },
      paint: {
        'line-color': style.color,
        'line-width': widthExpr(base),
        'line-opacity': style.opacity,
      },
    });
  }
}

/** Trail-to-street links, drawn as what they are: a hint that you can get
 *  between the two here, not a facility. */
export function addConnectorLayer(map) {
  for (const src of SOURCES) {
    const id = `${src}-connectors`;
    if (map.getLayer(id) || !map.getSource(src)) continue;
    map.addLayer({
      id, type: 'line', source: src,
      filter: ['==', ['coalesce', ['get', 'fac'], 0], FAC_CONNECTOR],
      layout: { 'line-cap': 'round' },
      paint: {
        'line-color': LTS[1].color,
        'line-width': ['interpolate', ['linear'], ['zoom'], 13, 0.8, 17, 2],
        'line-opacity': connectorOpacity(),
      },
    });
  }
}

/** Every interactive layer, topmost first, so a click prefers what is drawn on top. */
export function hitLayers(map) {
  return LAYERS
    .map(layerId)
    .filter((id) => map.getLayer(id))
    .reverse();
}

export function setLtsVisible(map, lts, visible) {
  const v = visible ? 'visible' : 'none';
  for (const entry of LAYERS.filter((e) => e.lts === lts)) {
    const id = layerId(entry);
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', v);
    if (map.getLayer(`${id}-casing`)) {
      map.setLayoutProperty(`${id}-casing`, 'visibility', v);
    }
  }
}

export function setSourceVisible(map, src, visible) {
  const v = visible ? 'visible' : 'none';
  for (const entry of LAYERS.filter((e) => e.src === src)) {
    const id = layerId(entry);
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', v);
    if (map.getLayer(`${id}-casing`)) {
      map.setLayoutProperty(`${id}-casing`, 'visibility', v);
    }
  }
}

/** Inline SVG swatch matching the map's solid LTS line. */
export function swatchSvg(lts) {
  const s = LTS[lts];
  return `<svg class="swatch" viewBox="0 0 34 12" aria-hidden="true">
    <line x1="1" y1="6" x2="33" y2="6" stroke="${s.color}"
          stroke-width="${Math.max(3, s.width)}" stroke-linecap="round"/>
  </svg>`;
}


/* ------------------------------------------------------------------- route */

/**
 * Route rendering: a bright casing with each segment painted in its LTS color.
 *
 * Keeping the same palette as the network makes every comfort change along the
 * trip visible. The white casing and faded network context distinguish route
 * segments from nearby streets even when they share a color.
 */
export function addRouteLayers(map) {
  if (map.getSource('route')) return;
  const empty = { type: 'FeatureCollection', features: [] };
  map.addSource('route', { type: 'geojson', data: empty });
  map.addSource('route-endpoints', { type: 'geojson', data: empty });

  map.addLayer({
    id: 'route-casing', type: 'line', source: 'route',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': '#f8fafc',
      'line-width': ['case',
        ['==', ['get', 'bike_infra'], 1],
        ROUTE_FACILITY_CASING_WIDTH,
        ROUTE_STREET_CASING_WIDTH],
      'line-opacity': 0.95,
    },
  });
  map.addLayer({
    id: 'route-line', type: 'line', source: 'route',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': ['match', ['get', 'lts'],
        0, LTS[0].color,
        1, LTS[1].color,
        2, LTS[2].color,
        3, LTS[3].color,
        4, LTS[4].color,
        LTS[0].color],
      'line-width': ['case',
        ['==', ['get', 'bike_infra'], 1],
        ROUTE_FACILITY_WIDTH,
        ROUTE_STREET_WIDTH],
    },
  });
  map.addSource('route-access', { type: 'geojson', data: empty });
  map.addLayer({
    id: 'route-access', type: 'line', source: 'route-access',
    layout: { 'line-cap': 'round' },
    paint: {
      // Thin neutral access line: visible, but distinct from the coloured ride.
      'line-color': '#64748b', 'line-width': 3.5, 'line-opacity': 0.9,
    },
  });
  map.addLayer({
    id: 'route-endpoints', type: 'circle', source: 'route-endpoints',
    paint: {
      // Near-black start (A), signal-amber destination (B), matching the panel's
      // from/to pins.
      'circle-radius': 8,
      'circle-color': ['match', ['get', 'role'], 'from', '#172536', '#ff9f1c'],
      'circle-stroke-color': '#f8fafc',
      'circle-stroke-width': 3,
    },
  });
}

export function setRoute(map, featureCollection) {
  map.getSource('route')?.setData(
    featureCollection || { type: 'FeatureCollection', features: [] });
  setRouteFocus(map, Boolean(featureCollection?.features?.length));
}

/**
 * Fade the network beneath a route, then restore its configured opacities when
 * the route is cleared. Visibility remains untouched, so legend choices still
 * apply and every non-route segment stays present as quiet map context.
 */
function setRouteFocus(map, focused) {
  for (const entry of LAYERS) {
    const id = layerId(entry);
    if (map.getLayer(id)) {
      map.setPaintProperty(
        id, 'line-opacity',
        LTS[entry.lts].opacity * (focused ? ROUTE_CONTEXT_OPACITY : 1),
      );
    }
    if (map.getLayer(`${id}-casing`)) {
      map.setPaintProperty(
        `${id}-casing`, 'line-opacity', casingOpacity(focused));
    }
  }
  for (const src of SOURCES) {
    const id = `${src}-connectors`;
    if (map.getLayer(id)) {
      map.setPaintProperty(id, 'line-opacity', connectorOpacity(focused));
    }
  }
}

export function setRouteAccess(map, featureCollection) {
  map.getSource('route-access')?.setData(
    featureCollection || { type: 'FeatureCollection', features: [] });
}

export function setRouteEndpoints(map, points) {
  map.getSource('route-endpoints')?.setData({
    type: 'FeatureCollection',
    features: points.filter(Boolean).map((p) => ({
      type: 'Feature',
      properties: { role: p.role },
      geometry: { type: 'Point', coordinates: [p.lng, p.lat] },
    })),
  });
}
