/** Accessibility helpers.
 *
 * The hard problem on a canvas map is that features are not DOM nodes and
 * cannot receive focus. Rather than pretending otherwise, the app provides a
 * parallel non-map path (the "Browse streets" list) and announces map actions
 * through a live region so a screen-reader user is told what changed.
 */

let region = null;

export function announce(message) {
  region = region || document.getElementById('announcer');
  if (!region) return;
  // Clearing first guarantees a re-announcement when the text is unchanged.
  region.textContent = '';
  window.setTimeout(() => { region.textContent = message; }, 30);
}

export const prefersReducedMotion = () =>
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Coarse pointers need a much larger hit box: a 2.6px line is unhittable with
 *  a fingertip at MapLibre's default tolerance. */
export const isCoarsePointer = () =>
  window.matchMedia('(pointer: coarse)').matches;

export function easeTo(map, opts) {
  if (prefersReducedMotion()) map.jumpTo(opts);
  else map.easeTo({ duration: 600, ...opts });
}
