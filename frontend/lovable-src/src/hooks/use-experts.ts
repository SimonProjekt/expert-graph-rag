import { useState, useEffect, useRef, useCallback } from "react";
import type { ClearanceLevel, ExpertsResponse } from "@/types/api";
import { fetchExperts } from "@/lib/api-client";

export function useExperts(query: string, clearance: ClearanceLevel) {
  const [data, setData] = useState<ExpertsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const execute = useCallback(() => {
    if (!query.trim()) { setData(null); return; }
    abortRef.current?.abort();
    const c = new AbortController();
    abortRef.current = c;
    setLoading(true);
    setError(null);
    fetchExperts(query, clearance, c.signal)
      .then(r => { if (!c.signal.aborted) { setData(r); setLoading(false); } })
      .catch(e => { if (!c.signal.aborted) { setError(e.message); setLoading(false); } });
  }, [query, clearance]);

  useEffect(() => {
    if (!query.trim()) { setData(null); return; }
    const id = setTimeout(execute, 300);
    return () => { clearTimeout(id); abortRef.current?.abort(); };
  }, [query, clearance, execute]);

  return { data, loading, error, retry: execute };
}
