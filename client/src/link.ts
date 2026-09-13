/**
 * The view, in the address bar.
 *
 * `#empire=hive-bio-ships&tech=tech_titans&isolate=tech_titans`: the empire
 * profile, the pinned technology and the isolated lineage, by stable keys, so
 * a link copied from the address bar opens the same view. A hash rather than a
 * query string, because the page is static and the server never sees it.
 *
 * A key the dataset no longer has is ignored rather than guessed at.
 */

/** The empire value for every empire at once. */
export const ALL_EMPIRES = "all";

export interface LinkState {
  /** A profile key, `ALL_EMPIRES`, or null when the link does not say. */
  empire: string | null;
  tech: string | null;
  isolate: string | null;
}

export function readLink(): LinkState {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  return {
    empire: params.get("empire"),
    tech: params.get("tech"),
    isolate: params.get("isolate"),
  };
}

export function linkHash(state: LinkState): string {
  const params = new URLSearchParams();
  if (state.empire !== null) params.set("empire", state.empire);
  if (state.tech !== null) params.set("tech", state.tech);
  if (state.isolate !== null) params.set("isolate", state.isolate);
  const text = params.toString();
  return text ? `#${text}` : "";
}

/**
 * Put `state` in the address bar. `push` adds a history entry, so Back undoes
 * it; otherwise the current entry is replaced, which suits changes as frequent
 * as selecting a card.
 */
export function writeLink(state: LinkState, push: boolean): void {
  const hash = linkHash(state);
  if (hash === window.location.hash || (hash === "" && window.location.hash === "")) return;
  const url = `${window.location.pathname}${window.location.search}${hash}`;
  try {
    if (push) history.pushState(null, "", url);
    else history.replaceState(null, "", url);
  } catch {
    // Some embedded viewers refuse history changes; the view still works.
  }
}

/** The full address of `state`, for copying. */
export function linkUrl(state: LinkState): string {
  return `${window.location.origin}${window.location.pathname}${window.location.search}${linkHash(state)}`;
}
