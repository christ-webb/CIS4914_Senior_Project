"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface Polled<T> {
  data: T | null;
  online: boolean;
  loading: boolean;
  updatedAt: Date | null;
  refresh: () => Promise<void>;
}

// `load` must be referentially stable (module-level function or useCallback).
export function usePolling<T>(
  load: () => Promise<T>,
  intervalMs = 5000,
  enabled = true,
): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [online, setOnline] = useState(true);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const request = ++sequence.current;
    try {
      const result = await load();
      if (request !== sequence.current) return;
      setData(result);
      setOnline(true);
      setUpdatedAt(new Date());
    } catch {
      if (request !== sequence.current) return;
      setOnline(false);
    } finally {
      if (request === sequence.current) setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    const reset = () => { sequence.current += 1; setData(null); void refresh(); };
    const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("nm5-history") : null;
    if (channel) channel.onmessage = reset;
    window.addEventListener("nm5-history-reset", reset);
    return () => { window.removeEventListener("nm5-history-reset", reset); channel?.close(); sequence.current += 1; };
  }, [refresh]);

  useEffect(() => {
    const first = setTimeout(refresh, 0);
    if (!enabled || intervalMs <= 0) {
      return () => clearTimeout(first);
    }
    const timer = setInterval(refresh, intervalMs);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh, intervalMs, enabled]);

  return { data, online, loading, updatedAt, refresh };
}
