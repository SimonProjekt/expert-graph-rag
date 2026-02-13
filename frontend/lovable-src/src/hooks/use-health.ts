import { useState, useEffect } from "react";
import type { HealthResponse } from "@/types/api";
import { checkHealth } from "@/lib/api-client";

export function useHealth() {
  const [health, setHealth] = useState<HealthResponse | null>(null);

  useEffect(() => {
    const c = new AbortController();
    checkHealth(c.signal).then(setHealth).catch(() => {});
    const id = setInterval(() => {
      checkHealth().then(setHealth).catch(() => {});
    }, 30000);
    return () => { c.abort(); clearInterval(id); };
  }, []);

  return health;
}
