/**
 * Single source of truth for palette, layer order, and public-facing copy.
 *
 * The palette is the Transit control status ramp from the design record,
 * darkened slightly where necessary to remain legible on the production light
 * basemap. The draw order keeps low-stress streets above context roads.
 */

export const DATA_DIR = './data/';

/** LTS 0-4. There is no LTS 5; see params.toml.
 *  Colours use Transit control's mint, teal, signal amber and coral ramp.
 *  The rating semantics and widths are unchanged from the previous palette. */
export const LTS = {
  0: { color: '#68788b', width: 2.0, opacity: 0.50,
       short: 'Bikes not permitted', detail: 'Interstate or parkway' },
  1: { color: '#279b65', width: 5.0, opacity: 0.95,
       short: 'Relaxed', detail: 'Good for kids and new riders' },
  2: { color: '#126f57', width: 5.0, opacity: 0.95,
       short: 'Comfortable for most adults', detail: 'Quiet streets and bike lanes' },
  3: { color: '#ff9f1c', width: 4.0, opacity: 0.90,
       short: 'Busy', detail: 'For confident riders' },
  4: { color: '#d6423d', width: 4.0, opacity: 0.88,
       short: 'Stressful', detail: 'Experienced riders only' },
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
 * One layer per (source x LTS) gives explicit paint order and O(1) legend
 * filtering via `visibility` instead of rebuilding data-driven filters.
 *
 * Ordered by descending stress so low-stress paints on top, preserving the
 * previous map's deliberate fix. Within one rating, a built facility paints
 * above a plain street, so a bike lane is never hidden by the road it is on.
 */
/** Facility code for a synthetic path-to-street link. Excluded from the LTS
 *  layers: it is a routing artefact, not infrastructure anyone built. */
export const FAC_CONNECTOR = 7;

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

/** LFUCG functional road class, mirroring [rdclass.labels] in params.toml.
 *  Shown in segment details because both this class and the inferred lane count
 *  can explain why otherwise similar-looking blocks receive different ratings. */
export const ROAD_CLASS_PUBLIC = {
  1: 'Interstate / highway',
  2: 'Parkway / expressway',
  3: 'Arterial',
  4: 'Major collector',
  5: 'Minor collector',
  6: 'Local street',
  7: 'Service road',
  8: 'Alley',
};

/**
 * How hard the router works to avoid stress, as a rider-facing scale.
 *
 * `penalty` is a detour multiplier on distance: 2.5 on LTS 4 means the router
 * will ride 2.5 miles of quiet street rather than 1 mile of arterial, and — just
 * as importantly — will not ride 3. These are preferences, not physics. LTS 0 is
 * Infinity at every setting because cycling there is illegal, which is not a
 * preference a slider gets to override.
 *
 * The default is `balanced`. The single setting this replaced was `quiet`, and
 * at 6.0 it produced routes that were technically comfortable and practically
 * unusable: a rider shown a 29% detour to dodge two blocks of collector does not
 * take the detour, they take the collector and stop trusting the map. The old
 * behaviour is still here, one notch along, and the balanced route still says so
 * when it puts someone on a busy road.
 *
 * `balanced` is measured, not guessed. Over 120 pseudo-random trips of 1.5-8
 * direct miles on the real graph, medians were:
 *
 *     setting          detour   stress mi   LTS 4 mi   trips touching LTS 4
 *     direct            1.02      3.28        0.80            86%
 *     3:1.5 4:2.5       1.14      2.43        0.25            72%
 *     3:1.4 4:3.5       1.15      2.54        0.05            61%   <- chosen
 *     3:1.4 4:5.0       1.16      2.59        0.02            55%
 *     quiet 3:2.6 4:6   1.29      1.57        0.05            64%
 *
 * The knee is at 4:3.5 — the same detour as 4:2.5 for a fifth of the arterial
 * mileage. Keeping LTS 3 cheap (1.4) is what pays for it: busy collectors are
 * the affordable alternative to an arterial, so the router can treat LTS 4 as
 * expensive without going far out of its way. The rise in total stress miles is
 * that substitution, which is the trade worth making — "busy" instead of
 * "experienced riders only".
 *
 * Lives in config rather than in the router because both the router and the
 * slider need the same table, and the labels are public-facing copy.
 */
export const ROUTE_LEVELS = [
  { key: 'direct', label: 'Most direct', tick: 'Direct',
    hint: 'The short way. Dodges arterials only where it is nearly free.',
    penalty: { 0: Infinity, 1: 1.0, 2: 1.0, 3: 1.15, 4: 1.4 } },
  { key: 'balanced', label: 'Balanced', tick: null,
    hint: 'Trades a little distance for a lot of comfort.',
    penalty: { 0: Infinity, 1: 1.0, 2: 1.05, 3: 1.4, 4: 3.5 } },
  { key: 'quiet', label: 'Prefer quiet', tick: null,
    hint: 'Goes well out of the way to stay off busy roads.',
    penalty: { 0: Infinity, 1: 1.0, 2: 1.05, 3: 2.6, 4: 6.0 } },
  { key: 'only', label: 'Quiet streets only', tick: 'Quiet only',
    hint: 'Refuses rather than compromise — no route instead of a stressful one.',
    penalty: { 0: Infinity, 1: 1.0, 2: 1.0, 3: Infinity, 4: Infinity } },
];

export const DEFAULT_ROUTE_LEVEL = 1;
export const QUIETEST_ROUTE_LEVEL = ROUTE_LEVELS.length - 1;

/** `kind`, rendered in English. The previous map leaked the internal values
 *  (`bikeable_streets`, `unbikeable_without_infrastructure`) into the UI. */
export const KIND_PUBLIC = {
  0: 'Street',
  1: 'Off-road path',
  2: 'Connection to a path',
};

/** `basis` codes, mirroring lexbike/export.py. Only BASIS_MEASURED means the
 *  traffic figure came from a real KYTC count rather than an estimate. */
export const BASIS_TYPE_ONLY = 0;
export const BASIS_ESTIMATED = 1;
export const BASIS_MEASURED = 2;

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
