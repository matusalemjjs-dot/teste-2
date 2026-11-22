import type { ProjectStatus } from "./api";

export const STATUS_LABEL: Record<ProjectStatus, string> = {
  pending: "Aguardando vídeo", uploaded: "Vídeo enviado", queued: "Na fila", processing: "Processando",
  completed: "Concluído", failed: "Falhou", canceled: "Cancelado",
};

export const STATUS_STYLE: Record<ProjectStatus, string> = {
  pending: "bg-slate-100 text-slate-800", uploaded: "bg-sky-100 text-sky-900", queued: "bg-amber-100 text-amber-900",
  processing: "bg-indigo-100 text-indigo-900", completed: "bg-emerald-100 text-emerald-900",
  failed: "bg-red-100 text-red-900", canceled: "bg-slate-200 text-slate-700",
};

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  const m = Math.floor(s / 60);
  return m ? `${m} min ${String(s % 60).padStart(2, "0")} s` : `${s} s`;
}

export function minutes(value: number): string {
  return value.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export function bytes(n: number): string {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
