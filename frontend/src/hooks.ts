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

/** The admin key lives in sessionStorage only (gone when the tab closes); storage may be blocked, so it is optional. */
export function useAdminKey(): [string, (k: string) => void] {
  const [key, setKey] = useState<string>(() => {
    try {
      return sessionStorage.getItem(KEY) ?? "";
    } catch {
      return "";
    }
  });
  const update = (k: string) => {
    setKey(k);
    try {
      if (k) sessionStorage.setItem(KEY, k);
      else sessionStorage.removeItem(KEY);
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
