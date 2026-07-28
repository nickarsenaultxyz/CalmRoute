/**
 * Sharing.
 *
 * A bare "copy link" is not much use for advocacy. What travels is the *claim*,
 * so every share composes the statistic alongside the URL — a pasted link
 * carries its own argument rather than relying on the recipient to click,
 * wait for a map to load, and work out what they are looking at.
 *
 * The text is always generated from the live build (stats.json), never
 * hardcoded, so it cannot drift from what the map currently says.
 */

import { escapeHtml } from '../lib/format.js';

/** One sentence describing what the viewer is currently looking at. */
export function claim(stats, { segment } = {}) {
  if (segment) {
    const name = segment.nm || 'This street';
    return `${name} in Lexington is rated "${segment.ratingLabel}" for cycling.`;
  }
  if (!stats) return 'How comfortable is each Lexington street to ride a bike on?';
  const low = stats.low_stress;
  return `Lexington has ${low.miles.toLocaleString('en-US')} miles of streets `
    + `comfortable for an ordinary adult to bike on — but they are split into `
    + `${low.islands.toLocaleString('en-US')} disconnected islands, and the largest `
    + `holds only ${low.largest_island_share_pct}% of them.`;
}

export function render(stats, { segment } = {}) {
  const text = claim(stats, { segment });
  return `
    <p class="note">${escapeHtml(text)}</p>
    <p class="tech">The link below opens the map exactly as you have it now —
      same location, zoom and filters.</p>
    <div class="share-actions">
      <button class="btn primary" data-share="native">Share…</button>
      <button class="btn" data-share="copy">Copy link &amp; text</button>
      <button class="btn" data-share="email">Email a council member</button>
    </div>
    <p class="tech" id="share-status" role="status"></p>
    <p class="tech">
      Figures from the ${escapeHtml(stats?.generated?.slice(0, 10) || 'current')}
      build, ruleset ${escapeHtml(stats?.ruleset_version || '')}.
    </p>`;
}

export function mount(root, { stats, segment, announce } = {}) {
  const url = location.href;
  const text = claim(stats, { segment });
  const status = root.querySelector('#share-status');

  const say = (msg) => {
    if (status) status.textContent = msg;
    announce?.(msg);
  };

  root.querySelector('[data-share="native"]')?.addEventListener('click', async () => {
    // navigator.share needs a user gesture and is absent on most desktops;
    // fall through to the clipboard rather than failing silently.
    if (navigator.share) {
      try {
        await navigator.share({ title: 'Lexington Bike Stress Map', text, url });
        return;
      } catch (err) {
        if (err?.name === 'AbortError') return;   // user dismissed the sheet
      }
    }
    copy(`${text}\n\n${url}`, say);
  });

  root.querySelector('[data-share="copy"]')?.addEventListener('click', () => {
    copy(`${text}\n\n${url}`, say);
  });

  root.querySelector('[data-share="email"]')?.addEventListener('click', () => {
    const subject = 'Bike network connectivity in Lexington';
    const body = `${text}\n\nYou can see it on the map here:\n${url}\n\n`
      + 'I would like to see this addressed.\n';
    // mailto only; composing on the user's behalf and sending would be
    // presumptuous, and there is no server to send from anyway.
    location.href = `mailto:?subject=${encodeURIComponent(subject)}`
      + `&body=${encodeURIComponent(body)}`;
  });
}

async function copy(payload, say) {
  try {
    await navigator.clipboard.writeText(payload);
    say('Copied. Paste it anywhere.');
  } catch {
    say('Could not copy automatically — select the address bar and copy the URL.');
  }
}
