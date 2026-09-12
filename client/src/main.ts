/**
 * Wiring: load the dataset, drive the camera, route input.
 *
 * Input has three pointer gestures and a touch equivalent for each:
 *   hover / tap-select  -> light the technology's ancestry and descendants
 *   middle-click / long-press -> isolate it into a mini-tree
 *   right-click / tap   -> detail popup
 */

import { Camera } from "./camera";
import { Renderer, TIER_HEADER_PX, emptySelection } from "./renderer";
import { expand, type Dataset, type RawDataset } from "./types";

const LONG_PRESS_MS = 450;
const LONG_PRESS_SLOP = 12;
/** Rendered size of a perk icon in the detail panel. */
const PERK_ICON_PX = 20;

async function main(): Promise<void> {
  const canvas = document.getElementById("tree") as HTMLCanvasElement;
  const status = document.getElementById("status") as HTMLElement;
  const panel = document.getElementById("panel") as HTMLElement;

  status.textContent = "Loading dataset…";
  const raw = (await fetch("data/dataset.json").then((r) => r.json())) as RawDataset;
  const data = expand(raw);

  const camera = new Camera(
    raw.canvas,
    { width: canvas.clientWidth, height: canvas.clientHeight },
    TIER_HEADER_PX,
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

  const counts = raw.meta.counts;
  const sources = raw.meta.sources.map((s) => `${s.name}${s.version ? ` ${s.version}` : ""}`);
  status.textContent =
    `${counts.technologies} technologies · ${counts.edges} dependencies · ` +
    `${counts.rows} rows · ${sources.join(" + ")}`;

  // -- selection ---------------------------------------------------------

  function relatives(index: number): { ancestors: Set<number>; descendants: Set<number> } {
    // Walk every slot sharing the technology key, so a relocated variant lights
    // up together with its primary.
    const seeds = data.byKey.get(data.nodes[index]!.key) ?? [index];
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

  function isolate(index: number | null): void {
    if (index === null) {
      selection.isolated = null;
    } else {
      const { ancestors, descendants } = relatives(index);
      const keep = new Set<number>([index, ...ancestors, ...descendants]);
      for (const sibling of data.byKey.get(data.nodes[index]!.key) ?? []) keep.add(sibling);
      selection.isolated = keep;
      selection.pinned = index;
      selection.ancestors = ancestors;
      selection.descendants = descendants;
      showPanel(index);
    }
    schedule();
  }

  // -- detail panel ------------------------------------------------------

  /** One condition in a gate: a perk, tradition, origin, civic or named trigger. */
  type Condition = { n: string; t: string; i?: number; c?: string };
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
  type Route = { k: "perk" | "tradition" | "tagged" | "research" | "start" | "event"; n: string };
  type Detail = { d: string; p: string[][]; ap?: Gate[]; u?: Route[]; st?: string[] };
  let details: Record<string, Detail> | null = null;
  async function showPanel(index: number): Promise<void> {
    const node = data.nodes[index]!;
    if (!details) {
      details = (await fetch("data/details.json").then((r) => r.json())) as typeof details;
    }
    // A variant slot's details are its swap's: its own name and description.
    const detail = details?.[node.swap ?? node.key] ?? details?.[node.key];
    const rowLabel = raw.rows[node.row]?.label ?? "";
    const prereqs = (detail?.p ?? [])
      .map((group) => group.map(nameOf).join(" <em>or</em> "))
      .map((line) => `<li>${line}</li>`)
      .join("");
    // Across every slot of the technology, since a dependent without a variant
    // of its own is wired to the primary slot only. One entry per name, so a
    // dependent with several presentations is not listed once per slot.
    const siblings = data.byKey.get(node.key) ?? [index];
    const outgoing = siblings.flatMap((i) => data.nodes[i]!.outgoing);
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
        ${gateFlag(detail?.ap)}
        ${tagFlag(node.tag, detail?.st)}
        ${node.variant ? '<span class="flag">Variant for Some Empires</span>' : ""}
        ${node.spilled ? '<span class="flag">Placed Past Its Tier Band</span>' : ""}
      </p>
      <p class="desc">${escapeHtml(detail?.d ?? "")}</p>
      ${gateSection(detail?.ap)}
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
   * Three kinds, kept apart because they are not the same claim. A `required`
   * gate means the technology does not exist for the empire without it; a
   * `granted` one that it is never drawn and this is what hands it out; an
   * `undrawable` one that it exists but is never offered without it, which
   * still leaves an event free to grant it. Each gate lists its alternatives,
   * so "Galactic Wonders" and "Genetic Ascension or Mechromancy" read as the
   * two separate requirements they are. An inherited gate says where it sits.
   */
  function gateSection(gates: Gate[] | undefined): string {
    if (!gates?.length) return "";
    const notes: Record<Gate["k"], string> = {
      required: "does not exist without it",
      granted: "only becomes researchable through it",
      undrawable: "exists, but is never offered for research without it",
    };
    const items = gates
      .map((gate) => {
        const alternatives = gate.a
          .map((alternative) =>
            alternative
              .map((c) => {
                const art = c.i !== undefined ? `<i class="perk-icon" style="${atlasStyle(c.i)}"></i>` : "";
                const context = c.c ? ` <span class="context">(${escapeHtml(c.c)})</span>` : "";
                return `<span class="condition">${art}${escapeHtml(c.n)}${context}</span>`;
              })
              .join(' <em class="join">+</em> '),
          )
          .join(' <em class="join">or</em> ');
        const note = gate.v
          ? `inherited through ${escapeHtml(gate.v)}, which ${notes[gate.k]}`
          : notes[gate.k];
        return `<li>${alternatives}<span class="note">${note}</span></li>`;
      })
      .join('<li class="and">and</li>');
    return `<h3>Requirements</h3><ul class="perks">${items}</ul>`;
  }

  /** How an undrawable technology reaches a player, when there is more to say than its tag. */
  function routeSection(routes: Route[] | undefined, tag: string | undefined): string {
    if (!routes?.length) return "";
    // A lone route the tag already names adds nothing.
    if (routes.length === 1 && routes[0]!.k === "tagged" && routes[0]!.n === tag) return "";
    const phrase: Record<Route["k"], (name: string) => string> = {
      perk: (n) => `Taking the ${n} ascension perk`,
      tradition: (n) => `Adopting ${n}`,
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
    const indices = data.byKey.get(key);
    const node = indices ? data.nodes[indices[0]!] : undefined;
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
    if (event.key === "Escape") {
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
        if (!seen.has(sibling)) seen.add(sibling);
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
