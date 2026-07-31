/** Presentation helpers. Everything user-visible about a number lives here. */

export const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function miles(n) {
  if (n == null) return null;
  if (n < 0.1) return `${Math.round(n * 5280)} ft`;
  return `${n < 10 ? n.toFixed(2) : n.toFixed(1)} mi`;
}

/**
 * Traffic volume, rounded and labelled by provenance.
 *
 * Rounded because an imputed 4,837 implies a precision that does not exist.
 *
 * `measured` is what keeps this honest. About 13% of segments have a real KYTC
 * count; the rest are modelled or imputed from class medians. Appending
 * "(2023 count)" to an estimated value -- which an earlier version did because a count year was
 * available globally -- puts "About 900 vehicles/day (2023 count)" directly
 * above a note saying no count exists for this street. Dating a number is only
 * honest when the number was actually measured.
 */
export function traffic(aadt, { year, measured } = {}) {
  if (aadt == null) return null;
  const rounded = aadt >= 10000 ? Math.round(aadt / 1000) * 1000
                : aadt >= 1000  ? Math.round(aadt / 100) * 100
                                : Math.round(aadt / 50) * 50;
  const n = rounded.toLocaleString('en-US');
  if (measured) {
    return year ? `About ${n} vehicles/day (${year} count)`
                : `About ${n} vehicles/day (measured)`;
  }
  return `About ${n} vehicles/day (estimated)`;
}

export const speed = (mph) => (mph == null ? null : `${mph} mph`);

/** Bike travel time at a plain 10 mph, deliberately hedged in the copy that
 *  uses it: the model ignores signals, stops and hills. */
export const minutes = (mi) => Math.max(1, Math.round((mi / 10) * 60));

export function plural(n, one, many) {
  return `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
}
