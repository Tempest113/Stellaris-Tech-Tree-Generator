/**
 * The guide: how to read the tree and how to use the page.
 *
 * One place for what a first visit needs and nothing on the canvas explains:
 * what a card's bar, badge and tag mean, what each kind of line says, when a
 * tier opens, and every way to select, isolate and share. A native <dialog>,
 * so focus is held inside it, Esc closes it, and focus returns to the button.
 *
 * Tier thresholds come from the dataset, not from here.
 */

import { AREA_ACCENT, EDGE, FLAG, INK } from "./theme";
import type { RawDataset } from "./types";

export interface Guide {
  open(): void;
}

export function mountGuide(button: HTMLButtonElement, dialog: HTMLDialogElement, raw: RawDataset): Guide {
  dialog.innerHTML = `
    <header>
      <h2 id="guide-title">Guide</h2>
      <button type="button" class="close" aria-label="Close guide">&times;</button>
    </header>
    <div class="body">
      <section>
        <h3>Reading the Tree</h3>
        <ul>
          <li><b>Columns are tiers</b>, left to right, with repeatable technologies in the last column.
            Within a tier, a card sits to the right of what it needs; the few that cannot fit are placed past their tier.</li>
          <li>${tierSentence(raw.tiers ?? [])}</li>
          <li><b>Rows are research categories</b>, washed in their area's colour:
            ${swatch(AREA_ACCENT.physics!)} Physics, ${swatch(AREA_ACCENT.society!)} Society,
            ${swatch(AREA_ACCENT.engineering!)} Engineering. Crisis rows take their crisis's colour,
            and a dot in a card's corner there gives its research area.</li>
        </ul>
      </section>

      <section>
        <h3>Cards</h3>
        <ul class="legend">
          <li>${bar(AREA_ACCENT.physics!)}<span><b>Side bar</b>: the research area, or
            ${swatch(FLAG.rare)} rare, or ${swatch(FLAG.dangerous)} dangerous. Rare and dangerous cards
            also carry ${marker("rare")} and ${marker("dangerous")} in their top corner.</span></li>
          <li>${badge(false)}<span><b>Badge</b>: the ascension perk or crisis path it needs.</span></li>
          <li>${badge(true)}<span><b>Dashed badge</b>: needed by something earlier in its line, not by the card itself.</span></li>
          <li><span class="sample tag">Event</span><span><b>Purple word</b>: how a technology that is never
            offered for research arrives, such as Event, Special Project or Debris, and the details panel lists every way.
            <b>Starting</b> marks one an ordinary empire begins with; the panel names the origins and civics that change that.</span></li>
          <li><span class="sample muted">Variant</span><span><b>Grey Variant</b>, with no empire chosen: the same
            technology, placed where the empires that see it differently find it.</span></li>
          <li><span class="sample mono">T3 ×5</span><span><b>Tier</b>, and levels for a repeatable (∞ for unlimited).
            The number beside it is the base research cost, before empire size, difficulty and other modifiers.</span></li>
        </ul>
      </section>

      <section>
        <h3>Lines</h3>
        <ul class="legend">
          <li>${line([])}<span><b>Solid</b>: a prerequisite.</span></li>
          <li>${line([4, 4])}<span><b>Short dashes</b>: one of several prerequisites; any one will do.</span></li>
          <li>${line([10, 6])}<span><b>Long dashes</b>: the technology does not exist for an empire until this one is researched.</span></li>
          <li>${line([], EDGE.ancestor)}<span><b>Gold</b>: what the selected technology needs.</span></li>
          <li>${line([], EDGE.descendant)}<span><b>Blue</b>: what it leads to.</span></li>
        </ul>
      </section>

      <section>
        <h3>Using the Page</h3>
        <ul class="keys">
          <li><span>Hover</span><span>Light up a technology's line</span></li>
          <li><span>Click or tap</span><span>Pin it, and open the details panel</span></li>
          <li><span>The arrow beside the details panel's close button</span><span>Fold the panel to its title bar, keeping the technology and its line lit</span></li>
          <li><span>Middle-click, long-press, or <b>Isolate</b> in the details panel</span><span>Show only that technology's line, with empty rows and columns closed up</span></li>
          <li><span>Drag · scroll or pinch</span><span>Pan · zoom</span></li>
          <li><span>Arrow keys, with the tree focused</span><span><kbd>←</kbd> something it needs, <kbd>→</kbd> something it leads to, <kbd>↑</kbd> <kbd>↓</kbd> the card above or below. Shift and an arrow pans, <kbd>+</kbd> <kbd>−</kbd> zoom, <kbd>Enter</kbd> hides or shows the details, <kbd>I</kbd> isolates</span></li>
          <li><span><kbd>/</kbd> or <kbd>Ctrl</kbd> <kbd>K</kbd></span><span>Search by name, or by key</span></li>
          <li><span><kbd>F</kbd></span><span>Fit the tree on screen</span></li>
          <li><span><kbd>Esc</kbd></span><span>Leave an isolated view, then close the details</span></li>
          <li><span><kbd>?</kbd></span><span>This guide</span></li>
        </ul>
        <p><b>Empire</b>: choose an authority, then what else fixes your empire's tree.
          Technologies it can never get are hidden, renamed ones take the name it sees,
          and requirements and ways in it cannot use are left out. Only combinations the
          empire designer allows are offered.</p>
        <p><b>Settings</b>: the Gigastructures settings preset chosen at the start of the game.
          A technology its settings switch off is hidden, and so is anything needing one.
          Choose <b>Non-Default Settings</b> if you changed settings by hand: nothing that
          depends on a setting is hidden then.</p>
        <p><b>Links</b>: the address bar keeps the empire, the pinned technology and any
          isolated line, so a copied address opens the same view. <b>Copy Link</b> in the details panel does this for you.</p>
      </section>

      <section>
        <h3>What the Tree Assumes</h3>
        <ul>
          <li>Every DLC is owned, Gigastructures' settings are the chosen preset's, and other game rules are at their defaults.</li>
          <li>Costs are base costs. A few, such as the Cosmic Storms technologies, change with
            Galactic Community resolutions, which the details panel lists.</li>
          <li>Built from ${sources(raw)}.</li>
        </ul>
      </section>
    </div>
  `;
  dialog.setAttribute("aria-labelledby", "guide-title");

  const close = () => dialog.close();
  dialog.querySelector(".close")?.addEventListener("click", close);
  // A click on the backdrop lands on the dialog itself, outside its content.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  dialog.addEventListener("close", () => button.focus());

  const open = () => {
    if (!dialog.open) dialog.showModal();
  };
  button.addEventListener("click", open);
  return { open };
}

/** "Tier 2 and up open once 6 of the tier before are researched", from the dataset. */
function tierSentence(tiers: [number, number][]): string {
  if (tiers.length === 0) return "Every tier is open from the start.";
  const counts = new Set(tiers.map(([, needed]) => needed));
  const first = Math.min(...tiers.map(([tier]) => tier));
  const consecutive = tiers.every(([tier], index) => tier === first + index);
  if (counts.size === 1 && consecutive) {
    const [needed] = counts;
    return (
      `<b>A tier opens in stages.</b> Nothing from Tier ${first} or above is offered for research ` +
      `until you have researched ${needed} technologies of the tier before it.`
    );
  }
  const parts = tiers.map(([tier, needed]) => `Tier ${tier} needs ${needed} of Tier ${tier - 1}`);
  return `<b>A tier opens in stages.</b> Nothing from a tier is offered until enough of the tier before are researched: ${parts.join("; ")}.`;
}

function sources(raw: RawDataset): string {
  const names = raw.meta.sources.map((s) => `${s.name}${s.version ? ` ${s.version}` : ""}`);
  const date = raw.meta.generated.slice(0, 10);
  return `${names.map(escapeHtml).join(" and ")}, on ${date}`;
}

/** The corner marker a rare or dangerous card carries, as drawn on the canvas. */
function marker(kind: "rare" | "dangerous"): string {
  const shape =
    kind === "rare"
      ? `<path d="M5 0.5 L9.5 5 L5 9.5 L0.5 5 Z" fill="${FLAG.rare}"/>`
      : `<path d="M5 0.5 L9.5 9.5 L0.5 9.5 Z" fill="${FLAG.dangerous}"/>` +
        `<rect x="4.25" y="3.5" width="1.5" height="3" fill="${INK.card}"/><rect x="4.25" y="7.2" width="1.5" height="1.5" fill="${INK.card}"/>`;
  return `<svg class="marker" width="10" height="10" viewBox="0 0 10 10" role="img" aria-label="${kind}">${shape}</svg>`;
}

function swatch(colour: string): string {
  return `<i class="swatch" style="background:${colour}" aria-hidden="true"></i>`;
}

function bar(colour: string): string {
  return (
    `<svg class="sample" width="46" height="26" viewBox="0 0 46 26" aria-hidden="true">` +
    `<rect x="0.5" y="0.5" width="45" height="25" rx="3" fill="${INK.card}" stroke="${INK.line}"/>` +
    `<rect x="0.5" y="0.5" width="3" height="25" rx="1.5" fill="${colour}"/></svg>`
  );
}

function badge(inherited: boolean): string {
  return (
    `<svg class="sample" width="46" height="26" viewBox="0 0 46 26" aria-hidden="true">` +
    `<circle cx="23" cy="13" r="10" fill="${INK.background}" stroke="${FLAG.rare}" stroke-width="1.5"` +
    `${inherited ? ' stroke-dasharray="3 2"' : ""}/>` +
    `<text x="23" y="17.5" text-anchor="middle" font-size="12" fill="${FLAG.rare}">✦</text></svg>`
  );
}

function line(dash: number[], colour: string = EDGE.idle): string {
  return (
    `<svg class="sample" width="46" height="26" viewBox="0 0 46 26" aria-hidden="true">` +
    `<line x1="2" y1="13" x2="44" y2="13" stroke="${colour}" stroke-width="2"` +
    `${dash.length ? ` stroke-dasharray="${dash.join(" ")}"` : ""}/></svg>`
  );
}

function escapeHtml(text: string): string {
  return text.replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c,
  );
}
