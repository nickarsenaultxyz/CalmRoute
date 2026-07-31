/** Data layer construction and visibility. */

import {
  FAC_CONNECTOR, LAYERS, LTS, SOURCES,
} from './config.js?v=20260731-field-notebook';

export const layerId = (e) => `${e.src}-lts${e.lts}`;

// Keep the rest of the network as geographic context while a route is shown,
// without letting it compete with the route itself.
const ROUTE_CONTEXT_OPACITY = 0.18;

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
  return ['interpolate', ['exponential', 1.4], ['zoom'],
    10, ['*', base * 0.40, boost],
    12, ['*', base * 0.70, boost],
    14, ['*', base * 1.00, boost],
    16, ['*', base * 1.75, boost],
    18, ['*', base * 3.00, boost]];
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
          // Paper-coloured casing so lines sit on the notebook page, not on
          // a white halo the ground no longer has.
          'line-color': 'rgba(251,248,240,0.85)',
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
        // Round caps swallow short dashes and the pattern silently disappears,
        // taking the non-colour channel with it.
        'line-cap': style.dash ? 'butt' : 'round',
        'line-join': 'round',
      },
      paint: {
        'line-color': style.color,
        'line-width': widthExpr(base),
        'line-opacity': style.opacity,
        // `line-dasharray` is a PAINT property, not layout. Putting it in
        // layout is rejected by the style spec ("unknown property") and every
        // dashed layer renders solid.
        //
        // It is also `property-type: cross-faded` with `parameters: ["zoom"]`,
        // so it accepts a constant or a zoom expression but NOT a data
        // expression -- ['match', ['get','lts'], ...] fails with "data
        // expressions not supported". That is why there is one layer per LTS
        // rather than a single data-driven layer.
        ...(style.dash ? { 'line-dasharray': style.dash } : {}),
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
      layout: { 'line-cap': 'butt' },
      paint: {
        'line-color': LTS[1].color,
        'line-width': ['interpolate', ['linear'], ['zoom'], 13, 0.8, 17, 2],
        'line-opacity': connectorOpacity(),
        'line-dasharray': [1, 1.5],
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

/** Inline SVG swatch matching the map exactly, dash pattern included, so the
 *  legend teaches the non-colour channel rather than only the colour. */
export function swatchSvg(lts) {
  const s = LTS[lts];
  const dash = s.dash ? ` stroke-dasharray="${s.dash.map((d) => d * 2.2).join(' ')}"` : '';
  return `<svg class="swatch" viewBox="0 0 34 12" aria-hidden="true">
    <line x1="1" y1="6" x2="33" y2="6" stroke="${s.color}"
          stroke-width="${Math.max(3, s.width)}" stroke-linecap="butt"${dash}/>
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
    paint: { 'line-color': '#fbf8f0', 'line-width': 12, 'line-opacity': 0.95 },
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
      'line-width': 6,
    },
  });
  map.addSource('route-access', { type: 'geojson', data: empty });
  map.addLayer({
    id: 'route-access', type: 'line', source: 'route-access',
    layout: { 'line-cap': 'round' },
    paint: {
      // Visible enough to read as "you have to get here yourself", distinct
      // enough from the solid route not to be mistaken for part of the ride.
      'line-color': '#6f6858', 'line-width': 3.5, 'line-opacity': 0.9,
      'line-dasharray': [1.5, 1.5],
    },
  });
  map.addLayer({
    id: 'route-endpoints', type: 'circle', source: 'route-endpoints',
    paint: {
      // Ink start (A), terracotta destination (B) — matching the panel's
      // from/to pins.
      'circle-radius': 8,
      'circle-color': ['match', ['get', 'role'], 'from', '#211e18', '#b3491c'],
      'circle-stroke-color': '#f4f0e6',
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
