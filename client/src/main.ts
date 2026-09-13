/**
 * Wiring: load the dataset, drive the camera, route input.
 *
 * Input has three pointer gestures and a touch equivalent for each:
 *   hover / tap-select  -> light the technology's ancestry and descendants
 *   middle-click / long-press -> isolate it into a mini-tree
 *   right-click / tap   -> detail popup
 */

import { Camera } from "./camera";
import { CARD_HEIGHT, CARD_WIDTH } from "./geometry";
import { mountProfilePicker } from "./profile";
import { mountSearch } from "./search";
import { Renderer, emptySelection } from "./renderer";
import {
  applyProfile,
  expand,
  mask,
  profileBit,
  type Dataset,
  type RawDataset,
} from "./types";

const LONG_PRESS_MS = 450;
const LONG_PRESS_SLOP = 12;
/** Rendered size of a perk icon in the detail panel. */
const PERK_ICON_PX = 20;
/**
 * Most lines the requirements are spelled out as, one complete way in per
 * line. Past this, multiplying gates out reads worse than listing them.
 */
const MAX_REQUIREMENT_LINES = 6;

async function main(): Promise<void> {
  const canvas = document.getElementById("tree") as HTMLCanvasElement;
  const status = document.getElementById("status") as HTMLElement;
  const panel = document.getElementById("panel") as HTMLElement;
  const profileBar = document.getElementById("profile") as HTMLElement;
  const toolbar = document.getElementById("toolbar") as HTMLElement;

  status.textContent = "Loading dataset…";
  const raw = (await fetch("data/dataset.json").then((r) => r.json())) as RawDataset;
  const data = expand(raw);

  const picker = mountProfilePicker(profileBar, raw, (profile) => {
    applyProfile(data, profile);
    renderer.refresh();
    selection.isolated = null;
    selection.pinned = null;
    select(null, true);
    camera.clamp();
    showStatus();
    schedule();
  });
  if (picker.current !== null) applyProfile(data, picker.current);

  const search = mountSearch(
    document.getElementById("search") as HTMLElement,
    data,
    atlasStyle,
    (index) => reveal(index),
  );

  // Fitting keeps the tree clear of the tier header and the toolbar below it.
  const camera = new Camera(
    () => data.view.geometry,
    { width: canvas.clientWidth, height: canvas.clientHeight },
    toolbar.getBoundingClientRect().bottom + 8,
  );
  const renderer = new Renderer(canvas, data, camera);
  renderer.resize();
  camera.fit();

  // Canvas text does not wait for web fonts: anything drawn before Chakra Petch
  // arrives uses the fallback and stays that way until the next redraw.
  void document.fonts.load(`600 14px "Chakra Petch"`).then(() => schedule());

  if (raw.atlas.sheets.length > 0) {
    // onload rather than decode(): decode() is allowed to defer indefinitely
    // while the document is hidden, which leaves the tree permanently iconless
    // for anyone who opens it in a background tab.
    const image = new Image();
    image.onload = () => {
      renderer.setAtlas(image);
      schedule();
    };
    image.onerror = () => {
      status.textContent += " · icons unavailable";
    };
    image.src = `data/${raw.atlas.sheets[0]}`;
  }

  const selection = emptySelection();
  let frame = 0;
  const schedule = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      renderer.draw(selection);
    });
  };

  const sources = raw.meta.sources.map((s) => `${s.name}${s.version ? ` ${s.version}` : ""}`);
  function showStatus(): void {
    const visible = data.nodes.filter((node) => !node.hidden);
    const technologies = new Set(visible.map((node) => node.key)).size;
    const rows = data.view.geometry.rows.filter((row) => row.shown).length;
    const profile = data.view.profile === null ? "All empires" : raw.profiles[data.view.profile]!.l;
    status.textContent =
      `${profile} · ${technologies} technologies · ${data.view.edges.length} dependencies · ` +
      `${rows} rows · ${sources.join(" + ")}`;
  }
  showStatus();

  // -- selection ---------------------------------------------------------

  function relatives(index: number): { ancestors: Set<number>; descendants: Set<number> } {
    // Walk every visible slot sharing the technology key, so a relocated variant
    // lights up together with its primary.
    const seeds = siblings(index);
    return {
      ancestors: walk(data, seeds, "incoming"),
      descendants: walk(data, seeds, "outgoing"),
    };
  }

  function select(index: number | null, pin: boolean): void {
    if (index === null) {
      selection.hovered = null;
      if (pin) selection.pinned = null;
      if (selection.pinned === null) {
        selection.ancestors.clear();
        selection.descendants.clear();
        hidePanel();
      }
      schedule();
      return;
    }
    if (pin) selection.pinned = selection.pinned === index ? null : index;
    selection.hovered = index;

    const active = selection.pinned ?? index;
    const { ancestors, descendants } = relatives(active);
    selection.ancestors = ancestors;
    selection.descendants = descendants;
    if (selection.pinned !== null) showPanel(active);
    schedule();
  }

  /**
   * Bring a card into view, pinned, with its panel open. Centred in the space
   * the panel leaves, so the panel never covers the card it describes.
   */
  function reveal(index: number): void {
    const node = data.nodes[index]!;
    selection.isolated = null;
    selection.pinned = null;
    const scale = Math.max(camera.scale, 0.8);
    const panelWidth = window.innerWidth > 700 ? Math.min(420, window.innerWidth) : 0;
    const offset = panelWidth / 2 / scale;
    camera.centreOn(node.x + CARD_WIDTH / 2 + offset, node.y + CARD_HEIGHT / 2, scale);
    select(index, true);
  }

  function isolate(index: number | null): void {
    if (index === null) {
      selection.isolated = null;
    } else {
      const { ancestors, descendants } = relatives(index);
      const keep = new Set<number>([index, ...ancestors, ...descendants]);
      for (const sibling of siblings(index)) keep.add(sibling);
      selection.isolated = keep;
      selection.pinned = index;
      selection.ancestors = ancestors;
      selection.descendants = descendants;
      showPanel(index);
    }
    schedule();
  }

  // -- detail panel ------------------------------------------------------

  /** Visible slots sharing a node's technology. */
  function siblings(index: number): number[] {
    return (data.byKey.get(data.nodes[index]!.key) ?? [index]).filter((i) => !data.nodes[i]!.hidden);
  }

  /** One condition in a gate: a perk, tradition, origin, civic, crisis level,
   *  named trigger or flag. `x` masks the profiles for which it can never be met. */
  type Condition = { n: string; t: string; i?: number; c?: string; x?: string };
  /**
   * `ap` is a conjunction of gates. Within a gate, `a` lists alternatives; each
   * alternative is conditions that must hold together. `v` names the
   * technology a gate is inherited from, when it is inherited.
   */
  type Gate = {
    k: "required" | "granted" | "undrawable";
    a: Condition[][];
    v?: string;
  };
  /** A way an undrawable technology reaches a player. */
  type Route = {
    k: "perk" | "tradition" | "crisis" | "tagged" | "research" | "start" | "event";
    n: string;
  };
  type Detail = { d: string; p: string[][]; ap?: Gate[]; u?: Route[]; st?: string[] };
  let details: Record<string, Detail> | null = null;
  async function showPanel(index: number): Promise<void> {
    const node = data.nodes[index]!;
    if (!details) {
      details = (await fetch("data/details.json").then((r) => r.json())) as typeof details;
    }
    // A variant slot's details are its swap's: its own name and description.
    const detail = details?.[node.swap ?? node.key] ?? details?.[node.key];
    const gates = forProfile(detail?.ap);
    const rowLabel = raw.rows[node.row]?.label ?? "";
    // A prerequisite option the profile never gets is no way in for it.
    const prereqs = (detail?.p ?? [])
      .map((group) => group.filter((key) => (data.byKey.get(key) ?? []).some((i) => !data.nodes[i]!.hidden)))
      .filter((group) => group.length > 0)
      .map((group) => group.map(nameOf).join(" <em>or</em> "))
      .map((line) => `<li>${line}</li>`)
      .join("");
    // Across every slot of the technology, since a dependent without a variant
    // of its own is wired to the primary slot only. One entry per name, so a
    // dependent with several presentations is not listed once per slot.
    const outgoing = siblings(index).flatMap((i) => data.nodes[i]!.outgoing);
    const dependents = [...new Set(outgoing.map((i) => data.nodes[i]!.name))]
      .slice(0, 24)
      .map((name) => `<li>${escapeHtml(name)}</li>`)
      .join("");

    panel.innerHTML = `
      <button class="close" aria-label="Close">&times;</button>
      <h2>${escapeHtml(node.name)}</h2>
      <p class="meta">
        Tier ${node.tier} · ${escapeHtml(node.area)} · ${escapeHtml(rowLabel)}
        ${node.cost ? ` · ${node.cost.toLocaleString()} research` : ""}
        ${node.levels !== undefined ? ` · ${node.levels < 0 ? "repeatable" : `${node.levels} levels`}` : ""}
      </p>
      <p class="flags">
        ${node.dangerous ? '<span class="flag danger">Dangerous</span>' : ""}
        ${node.rare ? '<span class="flag rare">Rare</span>' : ""}
        ${gateFlag(gates)}
        ${tagFlag(node.tag, detail?.st)}
        ${node.variant && data.view.profile === null ? '<span class="flag">Variant for Some Empires</span>' : ""}
        ${node.spilled ? '<span class="flag">Placed Past Its Tier Band</span>' : ""}
      </p>
      <p class="desc">${escapeHtml(detail?.d ?? "")}</p>
      ${gateSection(gates)}
      ${routeSection(detail?.u, node.tag)}
      <h3>Prerequisites ${prereqs ? "" : "<span class='none'>none</span>"}</h3>
      <ul>${prereqs}</ul>
      <h3>Unlocks ${dependents ? "" : "<span class='none'>nothing</span>"}</h3>
      <ul>${dependents}</ul>
      <p class="key">${escapeHtml(node.key)}</p>
    `;
    panel.hidden = false;
    panel.querySelector(".close")?.addEventListener("click", () => {
      hidePanel();
      selection.pinned = null;
      selection.isolated = null;
      schedule();
    });
  }

  function hidePanel(): void {
    panel.hidden = true;
  }

  /**
   * The gates as the current profile meets them.
   *
   * An alternative needing something the profile can never have is dropped:
   * a biological empire's Vat reads "Galactic Wonders and Genetic Ascension",
   * with no Mechromancy line.
   */
  function forProfile(gates: Gate[] | undefined): Gate[] | undefined {
    const profile = data.view.profile;
    if (!gates || profile === null) return gates;
    const bit = profileBit(profile);
    return gates
      .map((gate) => ({
        ...gate,
        a: gate.a.filter((alternative) => alternative.every((c) => (mask(c.x) & bit) === 0n)),
      }))
      .filter((gate) => gate.a.length > 0);
  }

  /** A gate as plain text: alternatives joined by "or", conditions by "+". */
  function gateText(gate: Gate): string {
    return gate.a.map((alternative) => alternative.map((c) => c.n).join(" + ")).join(" or ");
  }

  /** Short flag for the declared gates, with alternatives in brackets. */
  function gateFlag(gates: Gate[] | undefined): string {
    if (!gates?.length) return "";
    const own = gates.filter((g) => !g.v);
    const shown = own.length ? own : gates;
    const parts = shown.map((g) => (g.a.length > 1 && shown.length > 1 ? `(${gateText(g)})` : gateText(g)));
    const label = parts.length <= 2 ? parts.join(" and ") : `${parts.length} Requirements`;
    const verb = own.some((g) => g.k !== "undrawable") ? "Requires" : "Needs";
    return `<span class="flag perk">${verb} ${escapeHtml(label)}</span>`;
  }

  /** The card tag again, spelled out for the panel. */
  function tagFlag(tag: string | undefined, starting: string[] | undefined): string {
    if (!tag) return "";
    const text =
      tag === "Event"
        ? "Granted by an Event"
        : tag === "Starting"
          ? `Starting Technology${starting?.length ? ` for ${starting.join(", ")}` : ""}`
          : tag;
    return `<span class="flag event">${escapeHtml(text)}</span>`;
  }

  /**
   * The gates, spelled out.
   *
   * A technology's gates are a conjunction, and each gate a choice between
   * alternatives. Read as written -- "Galactic Wonders AND (Genetic Ascension
   * or Mechromancy)" -- the brackets are doing all the work, so the gates are
   * multiplied out instead: one line per complete way in, OR between lines.
   * The Vat reads "Galactic Wonders and Genetic Ascension / OR / Galactic
   * Wonders and Mechromancy". Only when that would run past
   * MAX_REQUIREMENT_LINES are the gates listed one by one with AND between.
   *
   * What kind of gate it is follows as a note. The kinds are not the same
   * claim: a `required` gate means the technology does not exist for the
   * empire without it; a `granted` one that it is never drawn and this is what
   * hands it out; an `undrawable` one that it exists but is never offered
   * without it, which still leaves an event free to grant it. An inherited gate
   * says which technology it comes through.
   */
  function gateSection(gates: Gate[] | undefined): string {
    if (!gates?.length) return "";
    const lines = requirementLines(gates);
    const body = lines
      ? lines.map((line) => `<li>${conjunction(line)}</li>`).join('<li class="or">or</li>')
      : gates
          .map((gate) => `<li>${gate.a.map(conjunction).join(' <em class="join">or</em> ')}</li>`)
          .join('<li class="or">and</li>');
    return `<h3>Requirements</h3><ul class="perks">${body}</ul>${gateNotes(gates)}`;
  }

  /** Every complete way to meet all of `gates`, or null when there are too many. */
  function requirementLines(gates: Gate[]): Condition[][] | null {
    let lines: Condition[][] = [[]];
    for (const gate of gates) {
      const next: Condition[][] = [];
      for (const line of lines) {
        for (const alternative of gate.a) {
          const merged = [...line];
          for (const condition of alternative) {
            if (!merged.some((c) => c.n === condition.n)) merged.push(condition);
          }
          next.push(merged);
        }
      }
      // A line that holds everything another line does, and more, is not a
      // separate way in: "A" already covers "A and B".
      const names = next.map((line) => new Set(line.map((c) => c.n)));
      lines = next.filter((_, i) => {
        const mine = names[i]!;
        return !names.some(
          (other, j) =>
            j !== i &&
            other.size <= mine.size &&
            [...other].every((n) => mine.has(n)) &&
            (other.size < mine.size || j < i),
        );
      });
      if (lines.length > MAX_REQUIREMENT_LINES) return null;
    }
    return lines;
  }

  /** Conditions that must hold together, joined by "and". */
  function conjunction(conditions: Condition[]): string {
    return conditions
      .map((c) => {
        const art = c.i !== undefined ? `<i class="perk-icon" style="${atlasStyle(c.i)}"></i>` : "";
        const context = c.c ? ` <span class="context">(${escapeHtml(c.c)})</span>` : "";
        return `<span class="condition">${art}${escapeHtml(c.n)}${context}</span>`;
      })
      .join(' <em class="join">and</em> ');
  }

  /** What kind of gates these are, and where inherited ones come from. */
  function gateNotes(gates: Gate[]): string {
    const notes: Record<Gate["k"], string> = {
      required: "does not exist without",
      granted: "only becomes researchable through",
      undrawable: "exists, but is never offered for research without",
    };
    const groups = new Map<string, Gate[]>();
    for (const gate of gates) {
      const key = `${gate.k}|${gate.v ?? ""}`;
      groups.set(key, [...(groups.get(key) ?? []), gate]);
    }
    const sentences = [...groups.values()].map((group) => {
      const { k, v } = group[0]!;
      const what =
        groups.size === 1
          ? group.length === 1 && group[0]!.a.length === 1 && group[0]!.a[0]!.length === 1
            ? "it"
            : "these"
          : group.map(gateText).join(" and ");
      const claim = `${notes[k]} ${what}`;
      return v ? `Inherited through ${v}, which ${claim}.` : `${capitalise(claim)}.`;
    });
    return sentences.map((s) => `<p class="gate-note">${escapeHtml(s)}</p>`).join("");
  }

  function capitalise(text: string): string {
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  /** How an undrawable technology reaches a player, when there is more to say than its tag. */
  function routeSection(routes: Route[] | undefined, tag: string | undefined): string {
    if (!routes?.length) return "";
    // A lone route the tag already names adds nothing.
    const lone = routes.length === 1 ? routes[0]! : undefined;
    if (lone && (lone.k === "tagged" || lone.k === "crisis") && lone.n === tag) return "";
    const phrase: Record<Route["k"], (name: string) => string> = {
      perk: (n) => `Taking the ${n} ascension perk`,
      tradition: (n) => `Adopting ${n}`,
      crisis: (n) => `Reaching ${n}`,
      tagged: (n) => n,
      research: (n) => `Researching ${n}`,
      start: () => "Game start, for some origins or empires",
      event: () => "An event, special project or situation",
    };
    const items = routes.map((r) => `<li>${escapeHtml(phrase[r.k](r.n))}</li>`).join("");
    return `<h3>Unlocked By</h3><ul>${items}</ul>`;
  }

  /** Background shorthand for one atlas cell, so a perk icon needs no <img>. */
  function atlasStyle(slot: number, size = PERK_ICON_PX): string {
    const { cell, perRow, perSheet, sheets, size: sheetPx } = raw.atlas;
    const sheet = Math.floor(slot / perSheet);
    const within = slot % perSheet;
    const scale = size / cell;
    const x = (within % perRow) * cell * scale;
    const y = Math.floor(within / perRow) * cell * scale;
    return (
      `background-image:url(data/${sheets[sheet]});` +
      `background-position:-${x}px -${y}px;` +
      `background-size:${sheetPx * scale}px ${sheetPx * scale}px;` +
      `width:${size}px;height:${size}px;`
    );
  }

  function nameOf(key: string): string {
    const indices = data.byKey.get(key) ?? [];
    const index = indices.find((i) => !data.nodes[i]!.hidden) ?? indices[0];
    const node = index === undefined ? undefined : data.nodes[index];
    return escapeHtml(node?.name ?? key);
  }

  // -- input -------------------------------------------------------------

  let dragging = false;
  let moved = false;
  let lastX = 0;
  let lastY = 0;
  let longPressTimer: number | undefined;
  let pressStart = { x: 0, y: 0 };

  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    dragging = true;
    moved = false;
    lastX = event.clientX;
    lastY = event.clientY;
    pressStart = { x: event.clientX, y: event.clientY };

    // Suppress Windows middle-click autoscroll before it starts.
    if (event.button === 1) event.preventDefault();

    if (event.pointerType === "touch") {
      longPressTimer = window.setTimeout(() => {
        longPressTimer = undefined;
        isolate(renderer.pick(event.clientX, event.clientY));
      }, LONG_PRESS_MS);
    }
  });

  canvas.addEventListener("pointermove", (event) => {
    if (dragging) {
      const dx = event.clientX - lastX;
      const dy = event.clientY - lastY;
      if (Math.abs(event.clientX - pressStart.x) > LONG_PRESS_SLOP ||
          Math.abs(event.clientY - pressStart.y) > LONG_PRESS_SLOP) {
        moved = true;
        if (longPressTimer) {
          clearTimeout(longPressTimer);
          longPressTimer = undefined;
        }
      }
      lastX = event.clientX;
      lastY = event.clientY;
      camera.panBy(dx, dy);
      schedule();
      return;
    }
    if (event.pointerType === "mouse" && selection.pinned === null) {
      const index = renderer.pick(event.clientX, event.clientY);
      if (index !== selection.hovered) select(index, false);
    }
  });

  canvas.addEventListener("pointerup", (event) => {
    dragging = false;
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      longPressTimer = undefined;
    }
    if (moved) return;
    const index = renderer.pick(event.clientX, event.clientY);
    if (event.button === 1) isolate(index);
    else select(index, true);
  });

  canvas.addEventListener("auxclick", (event) => event.preventDefault());
  canvas.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    const index = renderer.pick(event.clientX, event.clientY);
    if (index !== null) select(index, true);
  });

  canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      camera.zoomAt(event.clientX, event.clientY, Math.pow(0.999, event.deltaY));
      schedule();
    },
    { passive: false },
  );

  window.addEventListener("keydown", (event) => {
    const target = event.target as HTMLElement | null;
    if (target && (target.tagName === "INPUT" || target.tagName === "SELECT")) return;
    if (event.key === "/" || (event.key === "k" && (event.ctrlKey || event.metaKey))) {
      event.preventDefault();
      search.focus();
    } else if (event.key === "Escape") {
      selection.isolated = null;
      selection.pinned = null;
      select(null, true);
    } else if (event.key === "f") {
      camera.fit();
      schedule();
    }
  });

  window.addEventListener("resize", () => {
    renderer.resize();
    schedule();
  });

  // Redraw immediately when the tab becomes visible again: requestAnimationFrame
  // is paused while hidden, so a camera change made just before hiding would
  // otherwise sit unpainted.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) renderer.draw(selection);
  });

  // Debug hook. Lets a browser console, and automated visual checks, inspect
  // and drive the view without rAF, which is throttled when the tab is hidden.
  (window as unknown as Record<string, unknown>).__tree = {
    camera,
    renderer,
    data,
    selection,
    redraw: () => renderer.draw(selection),
  };

  schedule();
}

function walk(data: Dataset, seeds: number[], direction: "incoming" | "outgoing"): Set<number> {
  const seen = new Set<number>();
  const queue = [...seeds];
  while (queue.length > 0) {
    const current = queue.pop()!;
    for (const next of data.nodes[current]?.[direction] ?? []) {
      if (seen.has(next)) continue;
      seen.add(next);
      queue.push(next);
      for (const sibling of data.byKey.get(data.nodes[next]!.key) ?? []) {
        if (!seen.has(sibling) && !data.nodes[sibling]!.hidden) seen.add(sibling);
      }
    }
  }
  for (const seed of seeds) seen.delete(seed);
  return seen;
}

function escapeHtml(text: string): string {
  return text.replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c,
  );
}

void main();
