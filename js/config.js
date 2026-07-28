/**
 * Single source of truth for palette, layer order, and public-facing copy.
 *
 * The palette, weights and opacities are ported from the previous map's
 * LTS_COLORS / LTS_WEIGHTS / LTS_OPACITY, which had been tuned by eye over
 * several iterations. The draw order preserves that version's deliberate fix of
 * painting low-stress on top of the grey context roads.
 */

export const DATA_DIR = './data/';

/** LTS 0-4. There is no LTS 5; see params.toml. */
export const LTS = {
  0: { color: '#9ca3af', width: 2.0, opacity: 0.50,
       short: 'Bikes not permitted', detail: 'Interstate or parkway',
       dash: [1, 2] },
  1: { color: '#00875a', width: 5.0, opacity: 0.95,
       short: 'Relaxed', detail: 'Good for kids and new riders',
       dash: null },
  2: { color: '#2563eb', width: 5.0, opacity: 0.95,
       short: 'Comfortable for most adults', detail: 'Quiet streets and bike lanes',
       dash: null },
  3: { color: '#d97706', width: 4.0, opacity: 0.90,
       short: 'Busy', detail: 'For confident riders',
       dash: [3, 1.5] },
  4: { color: '#dc2626', width: 4.0, opacity: 0.85,
       short: 'Stressful', detail: 'Experienced riders only',
       dash: [2, 1.5] },
};

export const LTS_ORDER_LEGEND = [1, 2, 3, 4, 0];

/**
 * The three data sources, in fetch order. Each answers a different part of
 * "can I ride here", and they are disjoint — every feature is in exactly one.
 */
export const SOURCES = ['network', 'context', 'residential'];

/**
 * Draw order, bottom to top.
 *
 * One layer per (source x LTS) rather than a single data-driven layer, for a
 * reason that is not obvious and was verified against the style spec:
 * `line-dasharray` is `property-type: cross-faded` with `parameters: ["zoom"]`,
 * so a data expression is rejected outright ("data expressions not supported").
 * Splitting the layers is required for the dash patterns, and it happens to buy
 * two other things — explicit paint order, and O(1) legend filtering via
 * `visibility` instead of repeated `setFilter` calls.
 *
 * Ordered by descending stress so low-stress paints on top, preserving the
 * previous map's deliberate fix. Within one rating, a built facility paints
 * above a plain street, so a bike lane is never hidden by the road it is on.
 */
export const LAYERS = [
  { src: 'context',     lts: 0, casing: false },
  { src: 'context',     lts: 4, casing: false, scale: 0.8 },
  { src: 'network',     lts: 4, casing: true  },
  { src: 'context',     lts: 3, casing: false, scale: 0.8 },
  { src: 'network',     lts: 3, casing: true  },
  { src: 'residential', lts: 2, casing: false, scale: 0.55 },
  { src: 'network',     lts: 2, casing: true  },
  { src: 'residential', lts: 1, casing: false, scale: 0.55 },
  { src: 'network',     lts: 1, casing: true  },
];

/** Keyless raster basemaps, verified to return 200 without an API key.
 *  Every mainstream *vector* basemap now requires a key, so these are raster. */
export const BASEMAPS = {
  light: {
    label: 'Light',
    tiles: ['https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{ratio}.png',
            'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{ratio}.png',
            'https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{ratio}.png'],
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    maxzoom: 20,
  },
  gray: {
    label: 'Gray',
    tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}'],
    attribution: 'Esri, HERE, Garmin, &copy; OpenStreetMap contributors',
    maxzoom: 16,
  },
  satellite: {
    label: 'Satellite',
    tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    attribution: 'Esri, Maxar, Earthstar Geographics',
    maxzoom: 19,
  },
  // Genuinely useful, not a novelty: it maximises contrast for the LTS palette
  // and doubles as a high-contrast accessibility mode.
  none: { label: 'None', tiles: null, attribution: '', maxzoom: 22 },
};

export const DEFAULT_BASEMAP = 'light';

/** Codes mirror lexbike/export.py. Labels come from methodology.json at
 *  runtime; these are the fallbacks if that fetch fails. */
export const FAC = {
  0: 'none', 1: 'sharrow', 2: 'shoulder', 3: 'lane',
  4: 'buffered', 5: 'protected', 6: 'path', 7: 'connector',
};

export const FAC_PUBLIC = {
  0: 'No bike facility',
  1: 'Shared-lane markings',
  2: 'Paved shoulder',
  3: 'Bike lane',
  4: 'Buffered bike lane',
  5: 'Protected bike lane',
  6: 'Shared-use path',
  7: 'Path connection',
};

/** `kind`, rendered in English. The previous map leaked the internal values
 *  (`bikeable_streets`, `unbikeable_without_infrastructure`) into the UI. */
export const KIND_PUBLIC = {
  0: 'Street',
  1: 'Off-road path',
  2: 'Connection to a path',
};

export const CONFIDENCE = {
  0: { label: 'Low', note: 'This rating changes if reasonable assumptions change.' },
  1: { label: 'Medium', note: 'Traffic volume is estimated from similar streets.' },
  2: { label: 'High', note: 'Based on a measured traffic count or an off-road path.' },
};

/** Kept verbatim from the previous map's legend. It is the best copy in the
 *  old product and the honesty it sets is worth preserving exactly. */
export const EXISTING_ONLY_NOTE =
  '<strong>Existing network only.</strong> Ratings reflect built infrastructure; ' +
  'planned projects are shown separately.';
