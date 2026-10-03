// Minimal hash router: routes survive reloads and work from a static build.
import { useSyncExternalStore } from "react";

export type Route =
  | { name: "board" } | { name: "game"; id: string } | { name: "watchlist" }
  | { name: "portfolio" } | { name: "performance" } | { name: "research"; tab?: string }
  | { name: "settings"; tab?: string } | { name: "admin"; tab?: string }
  | { name: "login" } | { name: "signup" };

// Old dashboard tabs keep working: they redirect to their new homes.
const LEGACY: Record<string, string> = {
  games: "/board", game: "/board", tracker: "/portfolio", evaluation: "/performance",
  audit: "/research/audit", connections: "/admin/connections",
};

export function parse(hash: string): Route {
  const raw = hash.replace(/^#/, "") || "/board";
  const legacy = LEGACY[raw.replace(/^\//, "")];
  const p = (legacy ?? raw).split("/").filter(Boolean);
  switch (p[0]) {
    case "game": return p[1] ? { name: "game", id: decodeURIComponent(p[1]) } : { name: "board" };
    case "watchlist": return { name: "watchlist" };
    case "portfolio": return { name: "portfolio" };
    case "performance": return { name: "performance" };
    case "research": return { name: "research", tab: p[1] };
    case "settings": return { name: "settings", tab: p[1] };
    case "admin": return { name: "admin", tab: p[1] };
    case "login": return { name: "login" };
    case "signup": return { name: "signup" };
    default: return { name: "board" };
  }
}

const subs = new Set<() => void>();
window.addEventListener("hashchange", () => subs.forEach((f) => f()));

export function useRoute(): Route {
  const hash = useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => window.location.hash);
  return parse(hash);
}

export function go(path: string) {
  if (window.location.hash !== `#${path}`) window.location.hash = path;
}

export const href = (path: string) => `#${path}`;
