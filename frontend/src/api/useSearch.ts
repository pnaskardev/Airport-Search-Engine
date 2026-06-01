import { useState, useEffect, useRef, useCallback } from "react";
import type { SearchResult } from "../types/airport";

// Relative path: in production Caddy serves the app and the API on the same
// origin (/api/* -> backend), so no host or CORS is needed. Override with
// VITE_API_URL for local dev pointing at a remote backend.
const API_BASE = import.meta.env.VITE_API_URL ?? "";
const DEBOUNCE_MS = 200;

type Status = "idle" | "loading" | "success" | "error";

interface SearchState {
  result: SearchResult | null;
  status: Status;
  error: string | null;
}

const IDLE: SearchState = { result: null, status: "idle", error: null };

export function useSearch(query: string): SearchState {
  const [state, setState] = useState<SearchState>(IDLE);
  const abortRef = useRef<AbortController | null>(null);

  const search = useCallback(async (q: string) => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();

    setState({ result: null, status: "loading", error: null });

    try {
      const res = await fetch(
        `${API_BASE}/api/search?q=${encodeURIComponent(q)}`,
        { signal: abortRef.current.signal },
      );

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data: SearchResult = await res.json();
      setState({ result: data, status: "success", error: null });
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      setState({
        result: null,
        status: "error",
        error: "Search unavailable. Is the backend running?",
      });
    }
  }, []);

  useEffect(() => {
    const q = query.trim();

    if (!q) return;

    const timer = setTimeout(() => search(q), DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [query, search]);

  if (!query.trim()) {
    return IDLE;
  }

  return state;
}
