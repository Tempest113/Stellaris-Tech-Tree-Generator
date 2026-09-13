/**
 * Find a technology by name.
 *
 * Matches every name a technology goes by -- its own and each swap's, so
 * "Red Beam Projector" finds Red Lasers whichever profile is chosen -- and its
 * key, for anyone reading the game files alongside. Results are technologies,
 * not cards: a relocated technology is listed once.
 *
 * Ranking is plain and predictable: a name that starts with the query, then a
 * word in it that does, then the query anywhere, then the key. Ties go to the
 * name shown in the current view, then alphabetically.
 *
 * A technology the chosen profile does not get is still listed, marked as not
 * in this empire's tree, rather than silently missing.
 */

import type { Dataset } from "./types";

const MAX_RESULTS = 12;
/** Rendered size of a result's icon. */
const ICON_PX = 28;

interface Entry {
  key: string;
  /** Every name the technology goes by, with its lower-cased form for matching. */
  names: { text: string; lower: string }[];
}

interface Result {
  key: string;
  score: number;
  /** The slot to jump to, or null when the current profile shows none. */
  index: number | null;
  /** The name the query matched, when it is not the one shown. */
  matched?: string;
}

export function mountSearch(
  container: HTMLElement,
  data: Dataset,
  atlasStyle: (slot: number, size: number) => string,
  onPick: (index: number) => void,
): { focus(): void } {
  const input = document.createElement("input");
  input.type = "search";
  input.placeholder = "Search technologies";
  input.setAttribute("aria-label", "Search technologies");
  input.autocomplete = "off";
  input.spellcheck = false;

  const list = document.createElement("ul");
  list.className = "results";
  list.setAttribute("role", "listbox");
  list.hidden = true;

  container.append(input, list);

  const entries: Entry[] = [...data.byKey.entries()].map(([key, indices]) => {
    const names = new Set<string>();
    for (const index of indices) {
      const node = data.nodes[index]!;
      names.add(node.base.name);
      for (const presentation of node.presentations) names.add(presentation.name);
    }
    return { key, names: [...names].map((text) => ({ text, lower: text.toLowerCase() })) };
  });

  let results: Result[] = [];
  let active = 0;

  function score(entry: Entry, query: string): { score: number; matched: string } | null {
    let best: { score: number; matched: string } | null = null;
    for (const name of entry.names) {
      const at = name.lower.indexOf(query);
      if (at < 0) continue;
      const value = at === 0 ? 3 : /[\s\-(]/.test(name.lower[at - 1] ?? "") ? 2 : 1;
      if (!best || value > best.score) best = { score: value, matched: name.text };
    }
    if (!best && entry.key.toLowerCase().includes(query)) best = { score: 0.5, matched: entry.key };
    return best;
  }

  function run(): void {
    const query = input.value.trim().toLowerCase();
    if (!query) {
      results = [];
      render();
      return;
    }
    const found: Result[] = [];
    for (const entry of entries) {
      const match = score(entry, query);
      if (!match) continue;
      const indices = data.byKey.get(entry.key) ?? [];
      const index = indices.find((i) => !data.nodes[i]!.hidden) ?? null;
      const shown = index === null ? data.nodes[indices[0]!]! : data.nodes[index]!;
      found.push({
        key: entry.key,
        score: match.score,
        index,
        matched: match.matched !== shown.name ? match.matched : undefined,
      });
    }
    const nameOf = (r: Result) => data.nodes[r.index ?? data.byKey.get(r.key)![0]!]!.name;
    found.sort(
      (a, b) =>
        b.score - a.score ||
        Number(a.index === null) - Number(b.index === null) ||
        Number(a.matched !== undefined) - Number(b.matched !== undefined) ||
        nameOf(a).localeCompare(nameOf(b)),
    );
    results = found.slice(0, MAX_RESULTS);
    active = results.findIndex((r) => r.index !== null);
    render();
  }

  function render(): void {
    list.innerHTML = "";
    list.hidden = results.length === 0 && !input.value.trim();
    if (input.value.trim() && results.length === 0) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = "No technology matches";
      list.append(empty);
      return;
    }
    results.forEach((result, position) => {
      const node = data.nodes[result.index ?? data.byKey.get(result.key)![0]!]!;
      const item = document.createElement("li");
      item.setAttribute("role", "option");
      item.className = [
        position === active ? "active" : "",
        result.index === null ? "unavailable" : "",
      ].join(" ");
      item.setAttribute("aria-selected", String(position === active));

      const icon = document.createElement("i");
      icon.className = "icon";
      if (node.icon >= 0) icon.setAttribute("style", atlasStyle(node.icon, ICON_PX));

      const text = document.createElement("span");
      text.className = "text";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = node.name;
      const meta = document.createElement("span");
      meta.className = "meta";
      const row = data.raw.rows[node.row]?.label ?? "";
      const details = [`T${node.tier}`, row];
      if (result.matched) details.push(`also “${result.matched}”`);
      if (result.index === null) details.push("not in this empire's tree");
      meta.textContent = details.join(" · ");
      text.append(name, meta);
      item.append(icon, text);

      item.addEventListener("mousedown", (event) => {
        // mousedown, not click: a click would blur the input first and close the list.
        event.preventDefault();
        pick(position);
      });
      list.append(item);
    });
  }

  function pick(position: number): void {
    const result = results[position];
    if (!result || result.index === null) return;
    onPick(result.index);
    input.blur();
    list.hidden = true;
  }

  function move(step: number): void {
    if (results.length === 0) return;
    let next = active;
    for (let i = 0; i < results.length; i++) {
      next = (next + step + results.length) % results.length;
      if (results[next]!.index !== null) break;
    }
    active = next;
    render();
  }

  input.addEventListener("input", run);
  input.addEventListener("focus", () => {
    if (input.value.trim()) run();
  });
  input.addEventListener("blur", () => {
    list.hidden = true;
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      pick(active);
    } else if (event.key === "Escape") {
      input.value = "";
      results = [];
      input.blur();
    }
    // Keep tree shortcuts ("f" to fit, Escape to clear) out of the text box.
    event.stopPropagation();
  });

  return {
    focus() {
      input.focus();
      input.select();
    },
  };
}
