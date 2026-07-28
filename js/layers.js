/** Data layer construction and visibility. */

import { LAYERS, LTS, SOURCES } from './config.js';

export const layerId = (e) => `${e.src}-lts${e.lts}`;

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
      // Required for feature-state hover/selection to key off our own ids.
      promoteId: 'id',
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
    const filter = ['==', ['get', 'lts'], entry.lts];

    if (entry.casing) {
      map.addLayer({
        id: `${id}-casing`,
        type: 'line',
        source: entry.src,
        filter,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': 'rgba(255,255,255,0.85)',
          'line-width': widthExpr(base + 2.4),
          // A halo is noise when the city fits on screen.
          'line-opacity': ['interpolate', ['linear'], ['zoom'], 11, 0, 13, 1],
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
