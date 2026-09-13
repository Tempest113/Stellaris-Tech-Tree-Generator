/**
 * The empire profile picker.
 *
 * An authority and a handful of toggles. Only combinations an empire can
 * actually be created as are offered -- the dataset lists them -- so the picker
 * never lands on a tree nobody gets, and a toggle that cannot change for the
 * chosen authority is not shown at all. Turning a toggle on that needs others
 * (Wilderness needs a hive mind with bio-ships, and rules out Nomadic) moves the
 * rest to the nearest combination that allows it, and says what else changed.
 */

import type { RawDataset } from "./types";

const STORAGE_KEY = "techtree.profile";
const ALL = "all";

export interface ProfilePicker {
  /** The chosen profile's index into `dataset.profiles`, or null for every empire. */
  readonly current: number | null;
  /**
   * Show `index` as chosen without reporting a change or remembering it: a
   * link opening someone else's empire leaves the reader's own choice saved.
   */
  show(index: number | null): void;
}

interface State {
  authority: string | null;
  toggles: Set<string>;
}

export function mountProfilePicker(
  container: HTMLElement,
  raw: RawDataset,
  onChange: (profile: number | null) => void,
): ProfilePicker {
  const profiles = raw.profiles;
  const state: State = { authority: null, toggles: new Set() };
  let current: number | null = null;
  let note = "";

  const saved = read();
  const initial = profiles.findIndex((p) => p.k === saved);
  if (initial >= 0) {
    state.authority = profiles[initial]!.a;
    state.toggles = new Set(profiles[initial]!.t);
    current = initial;
  }

  function indexOf(authority: string | null, toggles: Set<string>): number | null {
    if (authority === null) return null;
    const found = profiles.findIndex(
      (p) => p.a === authority && p.t.length === toggles.size && p.t.every((t) => toggles.has(t)),
    );
    return found >= 0 ? found : null;
  }

  /** The valid profile under `authority` closest to `wanted`, keeping `fixed` as wanted. */
  function nearest(authority: string, wanted: Set<string>, fixed?: string): number | null {
    let best: number | null = null;
    let bestCost = Infinity;
    profiles.forEach((profile, index) => {
      if (profile.a !== authority) return;
      const on = new Set(profile.t);
      if (fixed !== undefined && on.has(fixed) !== wanted.has(fixed)) return;
      let cost = 0;
      for (const [name] of raw.toggles) if (on.has(name) !== wanted.has(name)) cost += 1;
      if (cost < bestCost) {
        best = index;
        bestCost = cost;
      }
    });
    return best;
  }

  /** `clicked` is the toggle the reader asked for, which the note need not repeat. */
  function choose(index: number | null, previous: Set<string>, clicked?: string): void {
    if (index === null) {
      state.toggles = new Set();
      note = "";
    } else {
      const profile = profiles[index]!;
      state.authority = profile.a;
      state.toggles = new Set(profile.t);
      const label = new Map(raw.toggles);
      const on = profile.t
        .filter((t) => !previous.has(t) && t !== clicked)
        .map((t) => label.get(t));
      const off = [...previous]
        .filter((t) => !state.toggles.has(t) && t !== clicked)
        .map((t) => label.get(t));
      note = [on.length ? `Also on: ${on.join(", ")}` : "", off.length ? `Also off: ${off.join(", ")}` : ""]
        .filter(Boolean)
        .join(" · ");
    }
    current = index;
    write(index === null ? ALL : profiles[index]!.k);
    render();
    onChange(current);
  }

  function render(): void {
    container.innerHTML = "";

    const select = document.createElement("select");
    select.setAttribute("aria-label", "Empire authority");
    select.append(new Option("All empires", ALL));
    for (const [key, label] of raw.authorities) select.append(new Option(label, key));
    select.value = state.authority ?? ALL;
    select.addEventListener("change", () => {
      const previous = new Set(state.toggles);
      if (select.value === ALL) {
        state.authority = null;
        choose(null, previous);
        return;
      }
      state.authority = select.value;
      const exact = indexOf(select.value, state.toggles);
      choose(exact ?? nearest(select.value, state.toggles), previous);
    });
    container.append(select);

    for (const [name, label] of raw.toggles) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      const on = state.toggles.has(name);
      button.className = on ? "toggle on" : "toggle";
      button.setAttribute("aria-pressed", String(on));
      const authority = state.authority;
      // Only toggles that can change for this authority are offered: a
      // Machine Intelligence has no Machine Species toggle to flip.
      const reachable =
        authority !== null && profiles.some((p) => p.a === authority && p.t.includes(name) !== on);
      if (!reachable) continue;
      button.addEventListener("click", () => {
        if (authority === null) return;
        const previous = new Set(state.toggles);
        const wanted = new Set(state.toggles);
        if (on) wanted.delete(name);
        else wanted.add(name);
        choose(nearest(authority, wanted, name), previous, name);
      });
      container.append(button);
    }

    if (note) {
      const hint = document.createElement("span");
      hint.className = "note";
      hint.textContent = note;
      container.append(hint);
    }
  }

  render();
  return {
    get current() {
      return current;
    },
    show(index: number | null) {
      const profile = index === null ? undefined : profiles[index];
      state.authority = profile?.a ?? null;
      state.toggles = new Set(profile?.t ?? []);
      current = profile ? index : null;
      note = "";
      render();
    },
  };
}

function read(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function write(value: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // Storage unavailable: the choice simply is not remembered.
  }
}
