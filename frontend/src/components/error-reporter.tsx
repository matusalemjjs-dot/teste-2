"use client";

import { useEffect } from "react";
import { reportClientError } from "@/lib/report";

/** Registra erros não tratados do navegador (JavaScript e promessas rejeitadas). */
export function ErrorReporter() {
  useEffect(() => {
    const onError = (e: ErrorEvent) => reportClientError(e.message, e.error?.stack, "window.onerror");
    const onRejection = (e: PromiseRejectionEvent) => {
      const r = e.reason;
      reportClientError(r instanceof Error ? r.message : String(r), r instanceof Error ? r.stack : undefined,
        "unhandledrejection");
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);
  return null;
}
