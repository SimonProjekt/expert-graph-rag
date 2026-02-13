import { useState, useEffect, useRef, useCallback } from "react";
import type { ClearanceLevel, SearchResponse } from "@/types/api";
import { searchPapers } from "@/lib/api-client";

export function useDebouncedSearch(
  query: string,
  clearance: ClearanceLevel,
  delay = 300
) {
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const execute = useCallback(() => {
    if (!query.trim()) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);

    searchPapers(query, clearance, 1, controller.signal)
      .then((res) => {
        if (!controller.signal.aborted) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          setError(err.message);
          setLoading(false);
        }
      });
  }, [query, clearance]);

  useEffect(() => {
    if (!query.trim()) {
      setData(null);
      setLoading(false);
      return;
    }
    const id = setTimeout(execute, delay);
    return () => {
      clearTimeout(id);
      abortRef.current?.abort();
    };
  }, [query, clearance, delay, execute]);

  const retry = useCallback(() => execute(), [execute]);

  return { data, loading, error, retry };
}
