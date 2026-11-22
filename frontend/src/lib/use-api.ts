"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";

/** Busca dados, opcionalmente em intervalo. `reload()` força nova busca. O estado só é atualizado no
 *  retorno da promessa (sem setState síncrono dentro do efeito). */
export function useApiData<T>(fetcher: () => Promise<T>, intervalMs: number | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let alive = true;
    const run = () => fetcher().then(
      (d) => { if (alive) { setData(d); setError(null); } },
      (e) => { if (alive) setError(e instanceof ApiError ? e : new ApiError(0, "Erro inesperado.")); },
    );
    run();
    const t = intervalMs ? setInterval(run, intervalMs) : null;
    return () => { alive = false; if (t) clearInterval(t); };
  }, [fetcher, intervalMs, tick]);

  return { data, error, reload };
}
