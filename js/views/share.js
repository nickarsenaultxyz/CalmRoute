/**
 * Sharing, and writing to the council member who represents a street.
 *
 * A bare "copy link" is not much use for advocacy. What travels is the *claim*,
 * so every share composes the statistic alongside the URL — a pasted link
 * carries its own argument rather than relying on the recipient to click, wait
 * for a map to load, and work out what they are looking at.
 *
 * The email is addressed by district. Which council member represents a street
 * depends on where the street is, so a single generic address would send most
 * messages to the wrong person. The roster comes from LFUCG's own published
 * directory, refreshed on every build.
 *
 * All text is generated from the live build, never hardcoded, so it cannot
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

function emailBody(stats, segment, rep) {
  const lines = [];
  lines.push(rep?.name ? `Dear Council Member ${rep.name.split(',')[0]},` : 'Dear Council Member,');
  lines.push('');
  if (segment?.nm) {
    lines.push(`I am writing about ${segment.nm}${
      rep ? `, which is in District ${rep.district}` : ''}.`);
    lines.push('');
    lines.push(`It is currently rated "${segment.rating}" for cycling`
      + `${segment.detail ? ` — ${segment.detail.toLowerCase()}` : ''}.`);
  } else {
    lines.push('I am writing about cycling conditions in Lexington.');
  }
  lines.push('');
  if (stats?.low_stress) {
    const low = stats.low_stress;
    lines.push(
      `Citywide, ${low.miles.toLocaleString('en-US')} miles of our streets are `
      + `comfortable for an ordinary adult to bike on, but they are split into `
      + `${low.islands.toLocaleString('en-US')} disconnected islands — the largest `
      + `holds only ${low.largest_island_share_pct}% of them. The quiet streets `
      + `exist; they simply do not connect.`);
    lines.push('');
  }
  lines.push('You can see the details on this map:');
  lines.push(location.href);
  lines.push('');
  lines.push('I would like to see this addressed.');
  lines.push('');
  lines.push('Thank you,');
  return lines.join('\n');
}

export function render(stats, { segment, council } = {}) {
  const text = claim(stats, { segment });
  const rep = member(council, segment);
  const fallback = council?.fallback;

  const emailBlock = rep?.email
    ? `<div class="rep">
         <b>District ${rep.district} — ${escapeHtml(rep.name || 'your council member')}</b>
         <span>${escapeHtml(rep.email)}</span>
       </div>
       <p class="tech">${segment?.nm ? `${escapeHtml(segment.nm)} is in this district.` : ''}
         The message is drafted for you; nothing is sent until you press send in
         your mail app.</p>`
    : `<p class="tech">${segment
        ? 'This street is outside the council district boundaries, so no representative could be matched.'
        : 'Select a street first and the message will be addressed to the council member who represents it.'}
       ${fallback ? `<a href="${escapeHtml(fallback.url)}" target="_blank"
          rel="noopener">${escapeHtml(fallback.label)}</a>.` : ''}</p>`;

  return `
    <p class="note">${escapeHtml(text)}</p>
    <p class="tech">The link below opens the map exactly as you have it now —
      same location, zoom and filters.</p>

    <div class="share-actions">
      <button class="btn primary" data-share="native">Share…</button>
      <button class="btn" data-share="copy">Copy link &amp; text</button>
    </div>

    <h2 class="section">Write to your council member</h2>
    ${emailBlock}
    <div class="share-actions">
      <button class="btn${rep?.email ? ' primary' : ''}" data-share="email"
        ${rep?.email ? '' : 'aria-describedby="no-rep"'}>
        ${rep?.email ? `Email ${escapeHtml((rep.name || '').split(',')[0] || 'them')}`
                     : 'Draft the email'}
      </button>
      <button class="btn" data-share="copy-email">Copy the message</button>
    </div>
    <p class="tech" id="share-status" role="status"></p>

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
  const body = emailBody(stats, segment, rep);
  const subject = segment?.nm
    ? `Cycling conditions on ${segment.nm}`
    : 'Bike network connectivity in Lexington';
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

  root.querySelector('[data-share="copy-email"]')
    ?.addEventListener('click', () => copy(
      `${rep?.email ? `To: ${rep.email}\n` : ''}Subject: ${subject}\n\n${body}`, say));

  root.querySelector('[data-share="email"]')?.addEventListener('click', () => {
    // mailto only. The draft opens in the sender's own mail app so they can
    // read and edit it -- sending on someone's behalf would be presumptuous,
    // and there is no server here to send from.
    const to = rep?.email ? encodeURIComponent(rep.email) : '';
    location.href = `mailto:${to}?subject=${encodeURIComponent(subject)}`
      + `&body=${encodeURIComponent(body)}`;
    say(rep?.name
      ? `Opening a draft to ${rep.name}. Nothing is sent until you press send.`
      : 'Opening a draft. Add a recipient before sending.');
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
