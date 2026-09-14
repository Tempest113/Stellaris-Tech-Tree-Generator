/**
 * Wiring: load the dataset, drive the camera, route input.
 *
 * Input has three pointer gestures and a touch equivalent for each:
 *   hover / tap-select  -> light the technology's ancestry and descendants
 *   middle-click / long-press -> isolate it into a mini-tree
 *   right-click / tap   -> detail popup
 * and two fingers pinch to zoom.
 *
 * The view -- empire, pinned technology, isolated lineage -- is mirrored in the
 * address bar (see `link.ts`), and a link opening the page restores it.
 */

import { Camera } from "./camera";
import { CARD_HEIGHT, CARD_WIDTH } from "./geometry";
import { mountGuide } from "./guide";
import { ALL_EMPIRES, linkUrl, readLink, writeLink, type LinkState } from "./link";
import { mountProfilePicker } from "./profile";
import { mountSearch } from "./search";
import { Renderer, TIER_HEADER_PX, emptySelection } from "./renderer";
import {
  applyView,
  closedInView,
  expand,
  hiddenByProfile,
  mask,
  profileBit,
  type Dataset,
  type RawDataset,
} from "./types";

const LONG_PRESS_MS = 450;
const LONG_PRESS_SLOP = 12;
/** Share of a phone screen the detail sheet covers; `#panel` in index.html agrees. */
const PHONE_SHEET = 0.7;
/** Height of the folded panel on a phone, before it has been measured. */
const FOLDED_SHEET_PX = 72;
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
  const dock = document.getElementById("dock") as HTMLElement;
  const settingsSection = document.getElementById("settings-section") as HTMLElement;
  const isolationBar = document.getElementById("isolation") as HTMLElement;
  const guideDialog = document.getElementById("guide") as HTMLDialogElement;
  const announcer = document.getElementById("announce") as HTMLElement;

  status.textContent = "Loading dataset…";
  const raw = (await fetch("data/dataset.json").then((r) => r.json())) as RawDataset;
  const data = expand(raw);
  if (raw.meta.title) document.title = raw.meta.title;

  const guide = mountGuide(document.getElementById("guide-button") as HTMLButtonElement, guideDialog, raw);

  settingsSection.hidden = !raw.presets?.length;
  const picker = mountProfilePicker(profileBar, document.getElementById("settings") as HTMLElement, raw, (profile, preset) => {
    applyView(data, profile, null, preset);
    isolatedRoot = null;
    refreshView();
    selection.pinned = null;
    select(null, true);
  });
  // A link's empire and preset win over the ones remembered, without replacing them.
  const link = readLink();
  const linkedPreset = presetFromLink(link.preset);
  const initialPreset = linkedPreset ?? picker.preset;
  const remembered = picker.current === null ? null : raw.profiles[picker.current]!.k;
  const linkedProfile = profileFromLink(link.empire ?? remembered ?? ALL_EMPIRES, initialPreset);
  if (link.empire !== null || linkedPreset !== null) picker.show(linkedProfile ?? null, initialPreset);
  applyView(data, picker.current, null, picker.preset);

  const search = mountSearch(
    document.getElementById("search") as HTMLElement,
    data,
    atlasStyle,
    (index) => reveal(index),
  );

  // On a phone the empire and settings fold away behind a button, and fold
  // again once the tree is touched.
  const dockToggle = document.getElementById("dock-toggle") as HTMLButtonElement;
  function openDock(open: boolean): void {
    dock.classList.toggle("open", open);
    dockToggle.setAttribute("aria-expanded", String(open));
  }
  dockToggle.addEventListener("click", () => openDock(!dock.classList.contains("open")));
  canvas.addEventListener("pointerdown", () => openDock(false));

  /** The dock's place on screen, or null while it takes none. */
  function dockArea(): { left: number; top: number; right: number; bottom: number } | null {
    const box = dock.getBoundingClientRect();
    return box.width > 0 ? { left: box.left, top: box.top, right: box.right, bottom: box.bottom } : null;
  }

  // Fitting keeps the tree clear of the tier header, and beside or below the dock.
  const camera = new Camera(
    () => data.view.geometry,
    { width: canvas.clientWidth, height: canvas.clientHeight },
    () => ({ header: TIER_HEADER_PX, dock: dockArea() }),
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
    const presetKey = data.view.profile === null ? data.view.preset : raw.profiles[data.view.profile]!.s;
    const preset = raw.presets?.find((p) => p.k === presetKey)?.l;
    status.textContent =
      `${profile}${preset ? ` · ${preset}` : ""} · ${technologies} technologies · ${data.view.edges.length} dependencies · ` +
      `${rows} rows · ${sources.join(" + ")}`;
  }
  showStatus();

  /** Everything that follows a change of view. */
  function refreshView(): void {
    renderer.refresh();
    camera.clamp();
    showIsolation();
    showStatus();
    schedule();
  }

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
      if (pin) syncLink(false);
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
    if (pin) syncLink(false);
    schedule();
  }

  /**
   * Bring a card into view, pinned, with its panel open. Centred in the space
   * the panel leaves, so the panel never covers the card it describes.
   */
  function reveal(index: number): void {
    if (data.nodes[index]!.hidden && data.view.isolated !== null) {
      applyView(data, data.view.profile, null);
      refreshView();
    }
    selection.pinned = null;
    centreCard(index, Math.max(camera.scale, 0.8));
    select(index, true);
  }

  /**
   * The screen area neither the dock nor the panel covers, with the panel as
   * if open. A narrow dock leaves the area beside it; a dock across the top of
   * a phone leaves the area below.
   */
  function openArea(): { left: number; top: number; right: number; bottom: number } {
    const insets = panelInsets();
    const box = dockArea();
    const beside = box !== null && box.right < window.innerWidth / 2;
    return {
      left: beside ? box.right : 0,
      top: beside || box === null ? TIER_HEADER_PX : box.bottom,
      right: window.innerWidth - insets.right,
      bottom: window.innerHeight - insets.bottom,
    };
  }

  /** Centre a card in the open area, at `scale`. */
  function centreCard(index: number, scale: number): void {
    const node = data.nodes[index]!;
    const area = openArea();
    const offsetX = (window.innerWidth / 2 - (area.left + area.right) / 2) / scale;
    const offsetY = (window.innerHeight / 2 - (area.top + area.bottom) / 2) / scale;
    camera.centreOn(node.x + CARD_WIDTH / 2 + offsetX, node.y + CARD_HEIGHT / 2 + offsetY, scale);
  }

  /** Move the view only as far as it takes to show a card the dock or panel now covers. */
  function keepCardInView(index: number): void {
    const node = data.nodes[index]!;
    const { scale, x, y } = camera;
    const left = x + node.x * scale;
    const top = y + node.y * scale;
    const area = openArea();
    const inside =
      left >= area.left &&
      top >= area.top &&
      left + CARD_WIDTH * scale <= area.right &&
      top + CARD_HEIGHT * scale <= area.bottom;
    if (!inside) centreCard(index, scale);
  }

  /**
   * Show only one technology's lineage -- everything it needs and everything
   * that needs it -- as a compact tree of its own: empty rows and columns close
   * up, and the view fits to it. Selecting inside it stays inside it, so no
   * trace can run to a card that is not drawn.
   *
   * The lineage is always read from the whole tree, so isolating another card
   * from inside an isolated view shows that card's full lineage.
   */
  function isolate(index: number | null): void {
    const profile = data.view.profile;
    if (index === null) {
      exitIsolation();
      return;
    }
    const root = data.nodes[index]!;
    if (data.view.isolated !== null) applyView(data, profile, null);
    const target = (data.byKey.get(root.key) ?? [index]).find((i) => !data.nodes[i]!.hidden) ?? index;
    const { ancestors, descendants } = relatives(target);
    const keep = new Set<number>([target, ...ancestors, ...descendants, ...siblings(target)]);
    applyView(data, profile, keep);
    isolatedRoot = target;
    refreshView();
    selection.pinned = null;
    // On a phone the details sheet would cover most of the lineage just
    // fitted, so it opens folded. Elsewhere the panel opens beside it.
    if (window.innerWidth <= 700) panelFolded = true;
    setPanelInsets(panelInsets());
    camera.fit();
    // Its own history entry, so Back returns to the whole tree.
    syncLink(true);
    select(target, true);
  }

  let isolatedRoot: number | null = null;
  /**
   * The details panel folded to its title bar, so the pinned technology's line
   * stays lit and in view. It stays folded from one selection to the next
   * until opened again: someone tapping through a line wants the line.
   */
  let panelFolded = false;

  /** Back to the whole tree, keeping the isolated card in view. */
  function exitIsolation(): void {
    if (data.view.isolated === null) return;
    const root = isolatedRoot;
    applyView(data, data.view.profile, null);
    isolatedRoot = null;
    refreshView();
    if (root !== null) {
      const node = data.nodes[root]!;
      camera.centreOn(node.x + CARD_WIDTH / 2, node.y + CARD_HEIGHT / 2, Math.max(camera.scale, 0.3));
    }
    selection.pinned = null;
    select(null, true);
  }

  function showIsolation(): void {
    const root = isolatedRoot;
    if (data.view.isolated === null || root === null) {
      isolationBar.hidden = true;
      isolationBar.innerHTML = "";
      return;
    }
    isolationBar.hidden = false;
    isolationBar.innerHTML = "";
    const label = document.createElement("span");
    label.textContent = `Isolated: ${data.nodes[root]!.name}`;
    const exit = document.createElement("button");
    exit.type = "button";
    exit.textContent = "Show Whole Tree";
    exit.title = "Esc";
    exit.addEventListener("click", () => exitIsolation());
    isolationBar.append(label, exit);
  }

  // -- the address bar ---------------------------------------------------

  /** Set while a link is being applied, so applying it writes no history of its own. */
  let applyingLink = false;

  function presetFromLink(preset: string | null): string | null {
    return raw.presets?.some((p) => p.k === preset) ? preset : null;
  }

  function profileFromLink(empire: string | null, preset: string | null): number | null | undefined {
    if (empire === null) return undefined;
    if (empire === ALL_EMPIRES) return null;
    const index = raw.profiles.findIndex((p) => p.k === empire && (p.s ?? null) === preset);
    return index >= 0 ? index : undefined;
  }

  /** The preset the view is read under. */
  function currentPreset(): string | null {
    const profile = data.view.profile;
    return profile === null ? data.view.preset : (raw.profiles[profile]!.s ?? null);
  }

  function linkState(): LinkState {
    const profile = data.view.profile;
    return {
      empire: profile === null ? ALL_EMPIRES : raw.profiles[profile]!.k,
      preset: currentPreset(),
      tech: selection.pinned === null ? null : data.nodes[selection.pinned]!.key,
      isolate: data.view.isolated === null || isolatedRoot === null ? null : data.nodes[isolatedRoot]!.key,
    };
  }

  function syncLink(push: boolean): void {
    if (!applyingLink) writeLink(linkState(), push);
  }

  /** A slot of `key` the current profile shows, preferring one on screen. */
  function slotFor(key: string | null): number | null {
    if (key === null) return null;
    const indices = data.byKey.get(key) ?? [];
    return (
      indices.find((i) => !data.nodes[i]!.hidden) ?? indices.find((i) => !hiddenByProfile(data, i)) ?? null
    );
  }

  /** Bring the view to what a link says. Anything it names that this dataset lacks is left as it is. */
  function applyLink(state: LinkState): void {
    applyingLink = true;
    try {
      const preset = presetFromLink(state.preset) ?? currentPreset();
      const found = profileFromLink(state.empire, preset);
      // A link naming a preset but no empire keeps the empire kind shown, under that preset.
      const profile =
        found !== undefined
          ? found
          : data.view.profile === null
            ? null
            : profileFromLink(raw.profiles[data.view.profile]!.k, preset) ?? null;
      if (profile !== data.view.profile || preset !== currentPreset()) {
        picker.show(profile, preset);
        applyView(data, profile, null, preset);
        isolatedRoot = null;
        refreshView();
        selection.pinned = null;
        select(null, true);
      }
      if (state.isolate !== linkState().isolate) {
        const root = slotFor(state.isolate);
        if (root !== null) isolate(root);
        else if (state.isolate === null) exitIsolation();
      }
      if (state.tech !== linkState().tech) {
        const target = slotFor(state.tech);
        if (target !== null) reveal(target);
        else if (state.tech === null) select(null, true);
      }
    } finally {
      applyingLink = false;
    }
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
  /** A project, situation or event a route runs through: `c` its kind, `x` the profiles it is closed to. */
  type RouteName = { n: string; c: string; x?: string };
  /** A way an undrawable technology reaches a player; `x` masks the profiles it is closed to. */
  type Route = {
    k: "perk" | "tradition" | "crisis" | "tagged" | "research" | "start" | "event";
    n: string;
    x?: string;
    s?: RouteName[];
  };
  /** A cost factor and the conditions it applies under, in words. */
  type CostModifier = { f: number; w: string };
  /** What an empire reads about starting with a technology: the first entry
   *  is for every empire at once, the rest for the profiles in `m`. */
  type StartWording = { t: string; m?: string };
  type Detail = {
    d: string;
    p: string[][];
    ap?: Gate[];
    u?: Route[];
    sw?: StartWording[];
    cm?: CostModifier[];
  };
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
    const isolatedHere =
      data.view.isolated !== null && isolatedRoot !== null && data.nodes[isolatedRoot]!.key === node.key;
    const links = raw.meta.links ?? {};
    const reportable = Boolean(links.issues || links.discord);

    panel.innerHTML = `
      <div class="head">
        <h2>${escapeHtml(node.name)}</h2>
        <button type="button" class="collapse" aria-controls="panel-body">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3.5 6l4.5 4.5L12.5 6" fill="none" stroke="currentColor" stroke-width="1.8"
              stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
        <button type="button" class="close" aria-label="Close details" title="Close (Esc)">&times;</button>
      </div>
      <div class="body" id="panel-body">
      <p class="meta">
        Tier ${node.tier} · ${escapeHtml(node.area)} · ${escapeHtml(rowLabel)}
        ${node.cost ? ` · ${node.cost.toLocaleString()} research` : ""}
        ${node.levels !== undefined ? ` · ${node.levels < 0 ? "repeatable" : `${node.levels} levels`}` : ""}
      </p>
      <p class="flags">
        ${node.dangerous ? '<span class="flag danger">Dangerous</span>' : ""}
        ${node.rare ? '<span class="flag rare">Rare</span>' : ""}
        ${gateFlag(gates)}
        ${tagFlag(node.tag)}
        ${node.variant && data.view.profile === null ? '<span class="flag">Variant for Some Empires</span>' : ""}
        ${node.spilled ? '<span class="flag">Placed Past Its Tier Band</span>' : ""}
      </p>
      <div class="actions">
        <button type="button" data-action="isolate">${isolatedHere ? "Show Whole Tree" : "Isolate"}</button>
        <button type="button" data-action="link">Copy Link</button>
        ${reportable ? '<button type="button" data-action="report" aria-expanded="false" aria-controls="report">Report a Problem</button>' : ""}
        <span class="done" role="status" aria-live="polite"></span>
      </div>
      ${reportable ? reportArea() : ""}
      ${startNote(detail?.sw)}
      <p class="desc">${escapeHtml(detail?.d ?? "")}</p>
      ${gateSection(gates)}
      ${routeSection(routesForProfile(detail?.u), node.tag)}
      ${costSection(detail?.cm)}
      <h3>Prerequisites ${prereqs ? "" : "<span class='none'>none</span>"}</h3>
      <ul>${prereqs}</ul>
      <h3>Unlocks ${dependents ? "" : "<span class='none'>nothing</span>"}</h3>
      <ul>${dependents}</ul>
      <p class="key">${escapeHtml(node.key)}</p>
      </div>
    `;
    panel.hidden = false;
    foldPanel(panelFolded);
    panel.querySelector(".close")?.addEventListener("click", (event) => {
      event.stopPropagation();
      selection.pinned = null;
      select(null, true);
    });
    // Folding or opening changes what the panel covers; the card it describes stays in sight.
    const toggleFold = (folded: boolean) => {
      foldPanel(folded);
      keepCardInView(index);
      schedule();
    };
    panel.querySelector(".collapse")?.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleFold(!panelFolded);
    });
    // Folded, the whole title bar opens it.
    panel.querySelector(".head")?.addEventListener("click", () => {
      if (panelFolded) toggleFold(false);
    });
    panel.querySelector('[data-action="isolate"]')?.addEventListener("click", () => {
      if (isolatedHere) exitIsolation();
      else isolate(index);
    });
    const done = panel.querySelector(".done") as HTMLElement;
    panel.querySelector('[data-action="link"]')?.addEventListener("click", async () => {
      const url = linkUrl(linkState());
      try {
        await navigator.clipboard.writeText(url);
        done.textContent = "Copied";
      } catch {
        // No clipboard access (an insecure origin, or refused): show it to copy by hand.
        done.textContent = url;
      }
    });
    const report = panel.querySelector("#report") as HTMLElement | null;
    const reportButton = panel.querySelector('[data-action="report"]');
    reportButton?.addEventListener("click", () => {
      if (!report) return;
      report.hidden = !report.hidden;
      reportButton.setAttribute("aria-expanded", String(!report.hidden));
    });
    const issue = panel.querySelector('[data-action="issue"]') as HTMLAnchorElement | null;
    if (issue && links.issues) issue.href = issueUrl(links.issues, node.name, reportText(node.name, node.key));
    const copied = panel.querySelector("#report .copied") as HTMLElement | null;
    panel.querySelector('[data-action="copy-report"]')?.addEventListener("click", async () => {
      const text = reportText(node.name, node.key);
      try {
        await navigator.clipboard.writeText(text);
        if (copied) copied.textContent = "Copied. Paste it into the Discord.";
      } catch {
        if (copied) copied.textContent = text;
      }
    });
  }

  /** The inline area Report a Problem opens: a pre-filled GitHub issue, and a report to copy for Discord. */
  function reportArea(): string {
    const links = raw.meta.links ?? {};
    const parts = ['<div class="report" id="report" hidden>', "<p>Tell us what looks wrong. The report says which technology, empire and settings you were looking at.</p>"];
    if (links.issues) {
      parts.push('<a class="report-link" data-action="issue" target="_blank" rel="noopener noreferrer" href="#">Open a GitHub Issue</a>');
    }
    if (links.discord) {
      parts.push(
        '<button type="button" data-action="copy-report">Copy Report for Discord</button>',
        `<a class="report-link" target="_blank" rel="noopener noreferrer" href="${escapeHtml(links.discord)}">Open the Discord</a>`,
      );
    }
    parts.push('<span class="copied" role="status" aria-live="polite"></span>', "</div>");
    return parts.join("");
  }

  /** A report of the view as plain text, the same for GitHub and Discord. */
  function reportText(name: string, key: string): string {
    const profile = data.view.profile === null ? "All empires" : raw.profiles[data.view.profile]!.l;
    const preset = raw.presets?.find((p) => p.k === currentPreset())?.l;
    return [
      `Technology: ${name} (${key})`,
      `Empire: ${profile}${preset ? `, ${preset}` : ""}`,
      `View: ${linkUrl(linkState())}`,
      "",
      "What looks wrong?",
      "",
    ].join("\n");
  }

  /** A new GitHub issue for `issues` (the repository's issues page), pre-filled. */
  function issueUrl(issues: string, name: string, body: string): string {
    const base = issues.replace(/\/+$/, "").replace(/\/new$/, "");
    const params = new URLSearchParams({ title: `${name}: `, body });
    return `${base}/new?${params.toString()}`;
  }

  /** Cost factors that apply under conditions, in the order the game lists them. */
  function costSection(modifiers: CostModifier[] | undefined): string {
    if (!modifiers?.length) return "";
    const items = modifiers
      .map((m) => {
        const tone = m.f < 1 ? "cheaper" : "dearer";
        return `<li><span class="factor ${tone}">×${m.f}</span> ${escapeHtml(capitalise(m.w))}</li>`;
      })
      .join("");
    return `<h3>Cost Changes</h3><ul class="costs">${items}</ul>`;
  }

  function hidePanel(): void {
    panel.hidden = true;
    setPanelInsets({ right: 0, bottom: 0 });
  }

  /** Fold the panel to its title bar, or open it out, leaving the selection as it is. */
  function foldPanel(folded: boolean): void {
    panelFolded = folded;
    panel.classList.toggle("collapsed", folded);
    const button = panel.querySelector(".collapse");
    button?.setAttribute("aria-expanded", String(!folded));
    button?.setAttribute("aria-label", folded ? "Show details" : "Hide details");
    button?.setAttribute("title", folded ? "Show details" : "Hide details");
    setPanelInsets(panelInsets());
    camera.clamp();
    schedule();
  }

  /**
   * What the panel covers: the right edge on a wide screen, the bottom on a
   * phone, where it is a sheet. Measured as if open, since isolating fits the
   * view before the panel appears. Folded beside the tree it covers only a
   * corner, which nothing needs to keep clear of; folded on a phone, a bar.
   */
  function panelInsets(): { right: number; bottom: number } {
    const phone = window.innerWidth <= 700;
    if (panelFolded) {
      if (!phone) return { right: 0, bottom: 0 };
      const shown = !panel.hidden && panel.classList.contains("collapsed");
      const measured = shown ? Math.round(panel.getBoundingClientRect().height) : 0;
      return { right: 0, bottom: measured || FOLDED_SHEET_PX };
    }
    if (!phone) return { right: Math.min(420, window.innerWidth), bottom: 0 };
    return { right: 0, bottom: Math.round(window.innerHeight * PHONE_SHEET) };
  }

  function setPanelInsets(insets: { right: number; bottom: number }): void {
    camera.rightInset = insets.right;
    camera.bottomInset = insets.bottom;
  }

  /**
   * The gates as the current profile meets them.
   *
   * An alternative needing something the profile can never have is dropped:
   * a biological empire's Vat reads "Galactic Wonders and Genetic Ascension",
   * with no Mechromancy line.
   */
  function forProfile(gates: Gate[] | undefined): Gate[] | undefined {
    if (!gates) return gates;
    return gates
      .map((gate) => ({
        ...gate,
        a: gate.a.filter((alternative) => alternative.every((c) => !closedInView(data, mask(c.x)))),
      }))
      .filter((gate) => gate.a.length > 0);
  }

  /** The ways in the current profile can take: a hive mind is not offered a militarist's project. */
  function routesForProfile(routes: Route[] | undefined): Route[] | undefined {
    if (!routes) return routes;
    return routes
      .filter((route) => !closedInView(data, mask(route.x)))
      .map((route) => ({ ...route, s: route.s?.filter((name) => !closedInView(data, mask(name.x))) }));
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
  function tagFlag(tag: string | undefined): string {
    if (!tag) return "";
    const text = tag === "Event" ? "Granted by an Event" : tag === "Starting" ? "Starting Technology" : tag;
    return `<span class="flag event">${escapeHtml(text)}</span>`;
  }

  /** Who begins the game with the technology, as the chosen empire reads it. */
  function startNote(wording: StartWording[] | undefined): string {
    if (!wording?.length) return "";
    const profile = data.view.profile;
    const entry =
      profile === null
        ? wording[0]
        : wording.slice(1).find((w) => (mask(w.m) & profileBit(profile)) !== 0n);
    return entry ? `<p class="start-note">${escapeHtml(entry.t)}</p>` : "";
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
    // A lone route the tag already names, with nothing more specific to say, adds nothing.
    const lone = routes.length === 1 ? routes[0]! : undefined;
    if (lone && (lone.k === "tagged" || lone.k === "crisis") && lone.n === tag && !lone.s?.length) return "";
    const phrase: Record<Route["k"], (name: string) => string> = {
      perk: (n) => `Taking the ${n} ascension perk`,
      tradition: (n) => `Adopting ${n}`,
      crisis: (n) => `Reaching ${n}`,
      tagged: (n) => n,
      research: (n) => `Researching ${n}`,
      start: () => "Game start",
      event: () => "An event, special project or situation",
    };
    const items = routes
      .map((r) => {
        const names = r.s ?? [];
        if (names.length === 0) return `<li>${escapeHtml(phrase[r.k](r.n))}</li>`;
        // A tag already says what kind of thing these are; a plain event route
        // names each kind, since one route can run through projects and events alike.
        const label = r.k === "tagged" ? r.n : [...new Set(names.map((name) => name.c))].join(" or ");
        return `<li><span class="route">${escapeHtml(label)}</span>${nameList(names)}</li>`;
      })
      .join("");
    return `<h3>Unlocked By</h3><ul class="routes">${items}</ul>`;
  }

  /** Names after a route's label: the first few inline, the rest behind a disclosure. */
  function nameList(names: RouteName[]): string {
    const SHOWN = 3;
    const text = (list: RouteName[]) => list.map((name) => escapeHtml(name.n)).join(", ");
    const head = `: ${text(names.slice(0, SHOWN))}`;
    if (names.length <= SHOWN) return `<span class="names">${head}</span>`;
    const rest = names.slice(SHOWN);
    return (
      `<span class="names">${head}</span>` +
      `<details class="more"><summary>and ${rest.length} more</summary>${text(rest)}</details>`
    );
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
  /** Pointers currently down, by id: two of them pinch. */
  const pointers = new Map<number, { x: number; y: number }>();
  /** The last pinch reading: finger spread and midpoint. */
  let pinch: { spread: number; x: number; y: number } | null = null;

  function cancelLongPress(): void {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      longPressTimer = undefined;
    }
  }

  function readPinch(): { spread: number; x: number; y: number } {
    const [a, b] = [...pointers.values()];
    return {
      spread: Math.hypot(a!.x - b!.x, a!.y - b!.y),
      x: (a!.x + b!.x) / 2,
      y: (a!.y + b!.y) / 2,
    };
  }

  canvas.addEventListener("pointerdown", (event) => {
    try {
      canvas.setPointerCapture(event.pointerId);
    } catch {
      // A synthetic or already-released pointer cannot be captured; the gesture still works.
    }
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size === 2) {
      // A second finger turns the gesture into a pinch: no tap, no long-press.
      cancelLongPress();
      moved = true;
      pinch = readPinch();
      return;
    }
    if (pointers.size > 2) return;
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
        // The finger lifting afterwards is the end of the press, not a tap.
        moved = true;
        isolate(renderer.pick(event.clientX, event.clientY));
      }, LONG_PRESS_MS);
    }
  });

  canvas.addEventListener("pointermove", (event) => {
    if (pointers.has(event.pointerId)) {
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    }
    if (pinch !== null && pointers.size >= 2) {
      const next = readPinch();
      if (pinch.spread > 0) camera.zoomAt(next.x, next.y, next.spread / pinch.spread);
      camera.panBy(next.x - pinch.x, next.y - pinch.y);
      pinch = next;
      schedule();
      return;
    }
    if (dragging) {
      const dx = event.clientX - lastX;
      const dy = event.clientY - lastY;
      if (Math.abs(event.clientX - pressStart.x) > LONG_PRESS_SLOP ||
          Math.abs(event.clientY - pressStart.y) > LONG_PRESS_SLOP) {
        moved = true;
        cancelLongPress();
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

  /** A pointer lifted or lost. Only the last one up can be a tap. */
  function release(event: PointerEvent, cancelled: boolean): void {
    pointers.delete(event.pointerId);
    cancelLongPress();
    if (pointers.size >= 2) {
      pinch = readPinch();
      return;
    }
    pinch = null;
    if (pointers.size === 1) {
      // Back to dragging with the finger left down, from where it is now.
      const [rest] = [...pointers.values()];
      lastX = rest!.x;
      lastY = rest!.y;
      return;
    }
    dragging = false;
    if (moved || cancelled) return;
    const index = renderer.pick(event.clientX, event.clientY);
    if (event.button === 1) isolate(index);
    else select(index, true);
  }

  canvas.addEventListener("pointerup", (event) => release(event, false));
  canvas.addEventListener("pointercancel", (event) => release(event, true));

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

  // -- keyboard ----------------------------------------------------------

  /** Say something to a screen reader, once. */
  function announce(text: string): void {
    announcer.textContent = "";
    // A fresh node each time, so repeating the same words is still announced.
    requestAnimationFrame(() => (announcer.textContent = text));
  }

  /** A technology in a sentence: what it is, where, and how it connects. */
  function describe(index: number): string {
    const node = data.nodes[index]!;
    const row = raw.rows[node.row]?.label ?? "";
    const needs = new Set(node.incoming.map((i) => data.nodes[i]!.key)).size;
    const leads = new Set(siblings(index).flatMap((i) => data.nodes[i]!.outgoing).map((i) => data.nodes[i]!.key)).size;
    const traits = [node.dangerous ? "dangerous" : "", node.rare ? "rare" : "", node.tag ?? ""].filter(Boolean);
    return (
      `${node.name}. Tier ${node.tier}, ${row}${traits.length ? `, ${traits.join(", ")}` : ""}. ` +
      `${needs} ${needs === 1 ? "prerequisite" : "prerequisites"}, leads to ${leads}.`
    );
  }

  /** The visible card nearest the middle of the open area. */
  function nearestToCentre(): number | null {
    const area = openArea();
    const centre = camera.toWorld((area.left + area.right) / 2, (area.top + area.bottom) / 2);
    let best: number | null = null;
    let distance = Infinity;
    data.nodes.forEach((node, index) => {
      if (node.hidden) return;
      const d = Math.hypot(node.x + CARD_WIDTH / 2 - centre.x, node.y + CARD_HEIGHT / 2 - centre.y);
      if (d < distance) {
        best = index;
        distance = d;
      }
    });
    return best;
  }

  /**
   * The card an arrow key steps to from `from`: left to the nearest thing it
   * needs, right to the nearest thing it leads to, up and down through its
   * column.
   */
  function step(from: number, key: string): number | null {
    const node = data.nodes[from]!;
    const nearest = (candidates: number[]) =>
      candidates
        .filter((i) => !data.nodes[i]!.hidden)
        .sort(
          (a, b) =>
            Math.abs(data.nodes[a]!.y - node.y) - Math.abs(data.nodes[b]!.y - node.y) ||
            Math.abs(data.nodes[a]!.x - node.x) - Math.abs(data.nodes[b]!.x - node.x),
        )[0] ?? null;
    if (key === "ArrowLeft") return nearest(node.incoming);
    if (key === "ArrowRight") return nearest(siblings(from).flatMap((i) => data.nodes[i]!.outgoing));
    const column = data.nodes
      .map((n, i) => [n, i] as const)
      .filter(([n]) => !n.hidden && n.column === node.column)
      .sort(([a], [b]) => a.y - b.y || a.x - b.x)
      .map(([, i]) => i);
    const position = column.indexOf(from) + (key === "ArrowUp" ? -1 : 1);
    return column[position] ?? null;
  }

  function stepTo(index: number): void {
    if (selection.pinned !== index) select(index, true);
    keepCardInView(index);
    schedule();
    announce(describe(index));
  }

  /** Keys that act on the tree while it, rather than a control, has focus. */
  function treeKey(event: KeyboardEvent): boolean {
    const PAN = 120;
    if (event.key.startsWith("Arrow") && event.shiftKey) {
      const moves: Record<string, [number, number]> = {
        ArrowLeft: [PAN, 0],
        ArrowRight: [-PAN, 0],
        ArrowUp: [0, PAN],
        ArrowDown: [0, -PAN],
      };
      const [dx, dy] = moves[event.key] ?? [0, 0];
      camera.panBy(dx, dy);
      schedule();
      return true;
    }
    if (event.key === "+" || event.key === "=" || event.key === "-") {
      camera.zoomAt(window.innerWidth / 2, window.innerHeight / 2, event.key === "-" ? 0.8 : 1.25);
      schedule();
      return true;
    }
    const current = selection.pinned;
    if (event.key.startsWith("Arrow")) {
      if (current === null) {
        const start = nearestToCentre();
        if (start !== null) stepTo(start);
        return true;
      }
      const next = step(current, event.key);
      if (next !== null) stepTo(next);
      else {
        const what = { ArrowLeft: "prerequisites", ArrowRight: "technologies it leads to" }[event.key as string];
        announce(what ? `${data.nodes[current]!.name} has no ${what} shown.` : "No more cards in this column.");
      }
      return true;
    }
    if (event.key === "Enter") {
      if (current === null) {
        const start = nearestToCentre();
        if (start !== null) stepTo(start);
      } else {
        foldPanel(!panelFolded);
        keepCardInView(current);
        schedule();
        announce(panelFolded ? "Details hidden." : "Details shown.");
      }
      return true;
    }
    if ((event.key === "i" || event.key === "I") && current !== null) {
      const here = data.view.isolated !== null && isolatedRoot !== null && data.nodes[isolatedRoot]!.key === data.nodes[current]!.key;
      if (here) {
        const key = data.nodes[current]!.key;
        exitIsolation();
        // Leaving keeps the keyboard where it was.
        const again = slotFor(key);
        if (again !== null) stepTo(again);
        announce("Showing the whole tree.");
      } else {
        const name = data.nodes[current]!.name;
        isolate(current);
        announce(`Isolated ${name} and its line.`);
      }
      return true;
    }
    return false;
  }

  window.addEventListener("keydown", (event) => {
    const target = event.target as HTMLElement | null;
    if (target && (target.tagName === "INPUT" || target.tagName === "SELECT")) return;
    // The open guide handles its own keys; Esc closes it natively.
    if (guideDialog.open) return;
    const onTree = !target || target === canvas || target === document.body;
    if (onTree && !event.ctrlKey && !event.metaKey && !event.altKey && treeKey(event)) {
      event.preventDefault();
      return;
    }
    if (event.key === "?") {
      event.preventDefault();
      guide.open();
    } else if (event.key === "/" || (event.key === "k" && (event.ctrlKey || event.metaKey))) {
      event.preventDefault();
      search.focus();
    } else if (event.key === "Escape") {
      if (dock.classList.contains("open")) {
        openDock(false);
        if (dock.contains(document.activeElement)) dockToggle.focus();
      } else if (data.view.isolated !== null) {
        exitIsolation();
      } else {
        selection.pinned = null;
        select(null, true);
      }
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
    isolate,
    select,
    reveal,
    applyLink,
  };

  // The technology and lineage a link names, once everything they need exists.
  applyLink(link);
  // Back and Forward, and a hash edited by hand.
  const followLink = () => applyLink(readLink());
  window.addEventListener("popstate", followLink);
  window.addEventListener("hashchange", followLink);

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
