/**
 * The empire profile picker.
 *
 * An authority and a handful of toggles, and the Gigastructures settings preset
 * the game is played with. Only combinations an empire can actually be created
 * as are offered -- the dataset lists them -- so the picker never lands on a
 * tree nobody gets, and a toggle that cannot change for the chosen authority is
 * not shown at all. Turning a toggle on that needs others (Wilderness needs a
 * hive mind with bio-ships, and rules out Nomadic) moves the rest to the nearest
 * combination that allows it, and says what else changed.
 *
 * Every empire under every preset is its own profile in the dataset; the
 * preset only chooses among them.
 */

import type { RawDataset } from "./types";

const STORAGE_KEY = "techtree.profile";
const PRESET_STORAGE_KEY = "techtree.preset";
const ALL = "all";

export interface ProfilePicker {
  /** The chosen profile's index into `dataset.profiles`, or null for every empire. */
  readonly current: number | null;
  /** The chosen preset's key, or null when the dataset has none. */
  readonly preset: string | null;
  /**
   * Show a profile and preset as chosen without reporting a change or
   * remembering them: a link opening someone else's empire leaves the
   * reader's own choice saved.
   */
  show(index: number | null, preset: string | null): void;
}

interface State {
  authority: string | null;
  toggles: Set<string>;
  preset: string | null;
}

export function mountProfilePicker(
  container: HTMLElement,
  /** Where the preset choice goes; hidden when the dataset has no presets. */
  settings: HTMLElement,
  raw: RawDataset,
  onChange: (profile: number | null, preset: string | null) => void,
): ProfilePicker {
  const profiles = raw.profiles;
  const presets = raw.presets ?? [];
  const validPreset = (key: string | null) => (presets.some((p) => p.k === key) ? key : null);
  const state: State = {
    authority: null,
    toggles: new Set(),
    preset: validPreset(read(PRESET_STORAGE_KEY)) ?? validPreset(raw.defaultPreset ?? null) ?? presets[0]?.k ?? null,
  };
  let current: number | null = null;
  let note = "";

  const inPreset = (index: number) => (profiles[index]!.s ?? null) === state.preset;

  const saved = read(STORAGE_KEY);
  const initial = profiles.findIndex((p, i) => p.k === saved && inPreset(i));
  if (initial >= 0) {
    state.authority = profiles[initial]!.a;
    state.toggles = new Set(profiles[initial]!.t);
    current = initial;
  }

  function indexOf(authority: string | null, toggles: Set<string>): number | null {
    if (authority === null) return null;
    const found = profiles.findIndex(
      (p, i) =>
        inPreset(i) && p.a === authority && p.t.length === toggles.size && p.t.every((t) => toggles.has(t)),
    );
    return found >= 0 ? found : null;
  }

  /** The valid profile under `authority` closest to `wanted`, keeping `fixed` as wanted. */
  function nearest(authority: string, wanted: Set<string>, fixed?: string): number | null {
    let best: number | null = null;
    let bestCost = Infinity;
    profiles.forEach((profile, index) => {
      if (profile.a !== authority || !inPreset(index)) return;
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
    write(STORAGE_KEY, index === null ? ALL : profiles[index]!.k);
    if (state.preset !== null) write(PRESET_STORAGE_KEY, state.preset);
    render();
    onChange(current, state.preset);
  }

  function render(): void {
    container.innerHTML = "";
    settings.innerHTML = "";

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
        authority !== null && profiles.some((p, i) => inPreset(i) && p.a === authority && p.t.includes(name) !== on);
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

    if (presets.length > 0) {
      const preset = document.createElement("select");
      preset.setAttribute("aria-label", "Gigastructures settings preset");
      preset.title = "Gigastructures settings preset";
      for (const option of presets) preset.append(new Option(option.l, option.k));
      preset.value = state.preset ?? "";
      preset.addEventListener("change", () => {
        state.preset = preset.value;
        const previous = new Set(state.toggles);
        choose(indexOf(state.authority, state.toggles), previous);
      });
      settings.append(preset);
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
    get preset() {
      return state.preset;
    },
    show(index: number | null, preset: string | null) {
      const profile = index === null ? undefined : profiles[index];
      state.preset = validPreset(preset ?? profile?.s ?? null) ?? state.preset;
      state.authority = profile?.a ?? null;
      state.toggles = new Set(profile?.t ?? []);
      current = profile ? index : null;
      note = "";
      render();
    },
  };
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage unavailable: the choice simply is not remembered.
  }
}
