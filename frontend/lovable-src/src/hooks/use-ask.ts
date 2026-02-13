import { useState, useRef, useCallback } from "react";
import type { ClearanceLevel, AskResponse } from "@/types/api";
import { askQuestion } from "@/lib/api-client";

export function useAsk(clearance: ClearanceLevel) {
  const [data, setData] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const ask = useCallback((question: string) => {
    if (!question.trim()) return;
    abortRef.current?.abort();
    const c = new AbortController();
    abortRef.current = c;
    setLoading(true);
    setError(null);
    setData(null);
    askQuestion(question, clearance, c.signal)
      .then(r => { if (!c.signal.aborted) { setData(r); setLoading(false); } })
      .catch(e => { if (!c.signal.aborted) { setError(e.message); setLoading(false); } });
  }, [clearance]);

  const retry = useCallback((q: string) => ask(q), [ask]);

  return { data, loading, error, ask, retry };
}
