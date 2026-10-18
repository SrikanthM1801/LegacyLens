import { useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data?: T;
  error?: Error;
  loading: boolean;
}

/**
 * Runs `fn` whenever `deps` change. While reloading it keeps the previous data (so charts hold their
 * frame instead of flashing a skeleton); a stale response never overwrites a newer one.
 */
export function useAsync<T>(fn: (signal: AbortSignal) => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ loading: true });
  const latest = useRef(0);
  useEffect(() => {
    const id = ++latest.current;
    const ctrl = new AbortController();
    setState((s) => ({ data: s.data, loading: true }));
    fn(ctrl.signal)
      .then((data) => id === latest.current && setState({ data, loading: false }))
      .catch((error: Error) => id === latest.current && error.name !== "AbortError" && setState({ error, loading: false }));
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

const KEY = "legacylens.adminKey";

/**
 * A key passed in the link as `#key=...` (a fragment is never sent to the server, the tunnel or their logs).
 * It is taken once, then removed from the address bar so it is not bookmarked or shared by accident.
 */
function keyFromLink(): string {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const k = params.get("key")?.trim() ?? "";
  if (k) {
    params.delete("key");
    const rest = params.toString();
    window.history.replaceState(null, "", window.location.pathname + window.location.search + (rest ? `#${rest}` : ""));
  }
  return k;
}

/**
 * The admin key is remembered in this browser (localStorage), so links from the Teams `@LegacyLens admin` card,
 * which open a new tab, go straight in. "Forget key" removes it. Storage may be blocked, so it is optional.
 */
export function useAdminKey(): [string, (k: string) => void] {
  const [key, setKey] = useState<string>(() => {
    const linked = keyFromLink();
    try {
      if (linked) localStorage.setItem(KEY, linked);
      return linked || (localStorage.getItem(KEY) ?? "");
    } catch {
      return linked;
    }
  });
  const update = (k: string) => {
    setKey(k);
    try {
      if (k) localStorage.setItem(KEY, k);
      else localStorage.removeItem(KEY);
      sessionStorage.removeItem(KEY); // where older builds kept it
    } catch {
      /* storage unavailable: the key just lasts for this page view */
    }
  };
  return [key, update];
}

/** Width of an element, tracked with ResizeObserver (a fixed fallback where it does not exist, e.g. jsdom). */
export function useWidth(ref: React.RefObject<HTMLElement>, fallback = 640): number {
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(240, Math.floor(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}
