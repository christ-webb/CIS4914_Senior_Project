"use client";

import { useCallback, useEffect, useState } from "react";

export interface Polled<T> {
  data: T | null;
  online: boolean;
  loading: boolean;
  updatedAt: Date | null;
  refresh: () => void;
}

// `load` must be referentially stable (module-level function or useCallback).
export function usePolling<T>(load: () => Promise<T>, intervalMs = 5000): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [online, setOnline] = useState(true);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await load());
      setOnline(true);
      setUpdatedAt(new Date());
    } catch {
      setOnline(false);
    } finally {
      setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const timer = setInterval(refresh, intervalMs);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh, intervalMs]);

  return { data, online, loading, updatedAt, refresh };
}
