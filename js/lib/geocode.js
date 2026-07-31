/**
 * Submit-only location search for route endpoints.
 *
 * The public Nominatim service permits moderate, user-triggered searches but
 * prohibits autocomplete and requires a maximum of one request per second.
 * Keep those service constraints here rather than scattering them through the
 * view. Results are cached for this page session so an identical query never
 * creates a second request.
 */

const MIN_REQUEST_INTERVAL_MS = 1100;
const cache = new Map();
let queue = Promise.resolve();
let lastRequestStarted = 0;

const endpoint = () =>
  document.querySelector('meta[name="lexbike-geocoder"]')?.content
  || 'https://nominatim.openstreetmap.org/search';

const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

function scheduledFetch(url) {
  const run = queue.then(async () => {
    const wait = MIN_REQUEST_INTERVAL_MS - (Date.now() - lastRequestStarted);
    if (wait > 0) await sleep(wait);
    lastRequestStarted = Date.now();
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Geocoder returned HTTP ${response.status}`);
    return response.json();
  });
  // A rejected request must not poison the queue for later searches.
  queue = run.catch(() => {});
  return run;
}

/**
 * Return up to five Lexington matches as `{ lng, lat, name }`.
 *
 * `bbox` follows the manifest's `[west, south, east, north]` order. Nominatim
 * expects its viewbox as `west,north,east,south`.
 */
export function searchLocations(query, bbox) {
  const cleaned = String(query || '').trim().replace(/\s+/g, ' ');
  if (cleaned.length < 3) return Promise.resolve([]);
  if (!Array.isArray(bbox) || bbox.length !== 4 || !bbox.every(Number.isFinite)) {
    return Promise.reject(new Error('The Lexington search boundary is unavailable.'));
  }

  const [west, south, east, north] = bbox;
  const key = `${cleaned.toLocaleLowerCase()}|${bbox.join(',')}`;
  if (cache.has(key)) return cache.get(key);

  const url = new URL(endpoint());
  url.searchParams.set('format', 'jsonv2');
  url.searchParams.set('q', cleaned);
  url.searchParams.set('viewbox', `${west},${north},${east},${south}`);
  url.searchParams.set('bounded', '1');
  url.searchParams.set('countrycodes', 'us');
  url.searchParams.set('limit', '5');
  url.searchParams.set('addressdetails', '1');
  url.searchParams.set('accept-language', 'en');

  const promise = scheduledFetch(url).then((rows) => rows
    .map((row) => ({
      lng: Number(row.lon),
      lat: Number(row.lat),
      name: String(row.display_name || row.name || '').trim(),
    }))
    .filter((row) => (
      Number.isFinite(row.lng)
      && Number.isFinite(row.lat)
      && row.lng >= west && row.lng <= east
      && row.lat >= south && row.lat <= north
      && row.name
    )));

  cache.set(key, promise);
  // Successful searches are reusable; a temporary outage should still be
  // retryable without forcing the rider to reload the page.
  promise.catch(() => cache.delete(key));
  return promise;
}
