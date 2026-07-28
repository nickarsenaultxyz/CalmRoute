/**
 * Sharing, and reaching the council member who represents a street.
 *
 * A bare "copy link" is not much use for advocacy. What travels is the *claim*,
 * so sharing composes the statistic alongside the URL — a pasted link carries
 * its own argument rather than relying on the recipient to click, wait for a
 * map to load, and work out what they are looking at.
 *
 * The email, by contrast, is deliberately empty. The map's job is to work out
 * *who* represents this street, which is the part a person cannot easily do
 * themselves; what to say is theirs. A prefilled message would also arrive as
 * an obvious form letter, which is exactly the kind of mail that gets counted
 * and discarded rather than read.
 *
 * All text here is generated from the live build, never hardcoded, so it cannot
 * drift from what the map currently says.
 */

import { escapeHtml } from '../lib/format.js';

/** One sentence describing what the viewer is currently looking at. */
export function claim(stats, { segment } = {}) {
  if (segment?.nm) {
    return `${segment.nm} in Lexington is rated "${segment.rating}" for cycling.`;
  }
  if (!stats) return 'How comfortable is each Lexington street to ride a bike on?';
  const low = stats.low_stress;
  return `Lexington has ${low.miles.toLocaleString('en-US')} miles of streets `
    + `comfortable for an ordinary adult to bike on — but they are split into `
    + `${low.islands.toLocaleString('en-US')} disconnected islands, and the largest `
    + `holds only ${low.largest_island_share_pct}% of them.`;
}

/** The seat representing the selected street, if we know it. */
function member(council, segment) {
  const d = segment?.district;
  if (d == null || !council?.districts) return null;
  const rec = council.districts[String(d)];
  return rec ? { district: d, ...rec } : null;
}

export function render(stats, { segment, council } = {}) {
  const text = claim(stats, { segment });
  const rep = member(council, segment);
  const fallback = council?.fallback;
  const firstName = (rep?.name || '').split(',')[0].trim();

  const contact = rep?.email
    ? `<div class="rep">
         <b>District ${rep.district} — ${escapeHtml(rep.name || 'your council member')}</b>
         <a href="mailto:${escapeHtml(rep.email)}">${escapeHtml(rep.email)}</a>
         ${rep.phone ? `<span>${escapeHtml(rep.phone)}</span>` : ''}
       </div>
       ${segment?.nm
         ? `<p class="tech">${escapeHtml(segment.nm)} is in this district.</p>` : ''}
       <div class="share-actions">
         <button class="btn primary" data-share="email">
           Email ${escapeHtml(firstName || 'them')}
         </button>
         ${rep.url ? `<a class="btn" href="${escapeHtml(rep.url)}" target="_blank"
            rel="noopener">Their council page</a>` : ''}
       </div>
       <p class="tech">Opens a blank message to ${escapeHtml(firstName || 'them')} in
         your mail app. What you write is up to you — a note in your own words
         carries more weight than a form letter.</p>`
    : `<p class="tech">${segment
        ? 'This street falls outside the council district boundaries, so no representative could be matched.'
        : 'Select a street and this will show the council member who represents it.'}
       ${fallback ? ` <a href="${escapeHtml(fallback.url)}" target="_blank"
          rel="noopener">${escapeHtml(fallback.label)}</a>.` : ''}</p>`;

  return `
    <p class="note">${escapeHtml(text)}</p>
    <p class="tech">The link below opens the map exactly as you have it now —
      same location, zoom and filters.</p>

    <div class="share-actions">
      <button class="btn primary" data-share="native">Share…</button>
      <button class="btn" data-share="copy">Copy link &amp; text</button>
    </div>
    <p class="tech" id="share-status" role="status"></p>

    <h2 class="section">Your council member</h2>
    ${contact}

    <p class="tech">
      Council contacts come from
      <a href="${escapeHtml(council?.source || '#')}" target="_blank" rel="noopener">LFUCG's
      published directory</a>, refreshed each time this map is rebuilt.
      Figures from the ${escapeHtml(stats?.generated?.slice(0, 10) || 'current')} build.
    </p>`;
}

export function mount(root, { stats, segment, council, announce } = {}) {
  const url = location.href;
  const text = claim(stats, { segment });
  const rep = member(council, segment);
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

  root.querySelector('[data-share="copy"]')
    ?.addEventListener('click', () => copy(`${text}\n\n${url}`, say));

  root.querySelector('[data-share="email"]')?.addEventListener('click', () => {
    if (!rep?.email) return;
    // Recipient only: no subject, no body. Working out who represents this
    // street is the part the map can do; the message is the sender's.
    location.href = `mailto:${encodeURIComponent(rep.email)}`;
    announce?.(`Opening a new message to ${rep.name || 'your council member'}.`);
  });
}

async function copy(payload, say) {
  try {
    await navigator.clipboard.writeText(payload);
    say('Copied. Paste it anywhere.');
  } catch {
    say('Could not copy automatically — select the text and copy it manually.');
  }
}
