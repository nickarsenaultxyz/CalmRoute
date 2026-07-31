/**
 * One panel, two form factors.
 *
 * Under 720px it is a bottom sheet with three snap points; above, a fixed side
 * panel. A view stack serves every screen (legend -> detail, search -> results
 * -> detail), and the panel's back button is wired to history.back() so it and
 * the browser back button are the same gesture rather than two competing ones.
 */

import { prefersReducedMotion } from './lib/a11y.js';

const SNAPS = ['peek', 'half', 'full'];

export class Panel {
  constructor({ onBack } = {}) {
    this.el = document.getElementById('panel');
    this.body = document.getElementById('panel-body');
    this.title = document.getElementById('panel-title');
    this.backBtn = document.getElementById('panel-back');
    this.handle = document.getElementById('sheet-handle');
    this.stack = [];
    this.onBack = onBack;

    this.backBtn.addEventListener('click', () => this.onBack?.());
    this._installDrag();
  }

  get isSheet() { return window.matchMedia('(max-width: 719px)').matches; }

  /** Replace the panel contents. `root` is true for a top-level view, which
   *  hides the back affordance. */
  show({ title, html, root = false, view = '', onMount } = {}) {
    this.title.textContent = title;
    if (view) this.el.dataset.view = view;
    else delete this.el.dataset.view;
    this.body.innerHTML = html;
    this.backBtn.hidden = root;
    this.body.scrollTop = 0;
    onMount?.(this.body);
    if (this.isSheet && this.snap === 'peek') this.setSnap('half');
    return this.body;
  }

  /** Move focus to the panel for keyboard and screen-reader users after a
   *  map interaction opens it. */
  focusBody() { this.body.focus({ preventScroll: true }); }

  get snap() { return this.el.dataset.snap; }

  setSnap(name) {
    if (!SNAPS.includes(name)) return;
    this.el.dataset.snap = name;
  }

  cycleSnap() {
    const i = SNAPS.indexOf(this.snap);
    this.setSnap(SNAPS[(i + 1) % SNAPS.length]);
  }

  _installDrag() {
    const h = this.handle;
    if (!h) return;

    // Tap or keyboard cycles the snap points; drag picks the nearest.
    h.addEventListener('click', () => this.cycleSnap());

    let startY = null;
    let startH = 0;

    const down = (e) => {
      if (!this.isSheet) return;
      startY = (e.touches ? e.touches[0] : e).clientY;
      startH = this.el.getBoundingClientRect().height;
      this.el.style.transition = 'none';
      h.style.cursor = 'grabbing';
    };
    const move = (e) => {
      if (startY == null) return;
      e.preventDefault();
      const y = (e.touches ? e.touches[0] : e).clientY;
      const h2 = Math.min(window.innerHeight * 0.92, Math.max(60, startH - (y - startY)));
      this.el.style.height = `${h2}px`;
    };
    const up = () => {
      if (startY == null) return;
      startY = null;
      h.style.cursor = '';
      const frac = this.el.getBoundingClientRect().height / window.innerHeight;
      this.el.style.height = '';
      if (!prefersReducedMotion()) this.el.style.transition = '';
      else this.el.style.transition = 'none';
      this.setSnap(frac < 0.28 ? 'peek' : frac < 0.72 ? 'half' : 'full');
    };

    h.addEventListener('touchstart', down, { passive: true });
    h.addEventListener('touchmove', move, { passive: false });
    h.addEventListener('touchend', up);
    h.addEventListener('mousedown', down);
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  }
}
