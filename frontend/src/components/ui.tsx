"use client";

import type { ProjectStatus } from "@/lib/api";
import { STATUS_LABEL, STATUS_STYLE } from "@/lib/format";

export function Button({ variant = "primary", className = "", ...props }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  const styles = {
    primary: "bg-indigo-700 text-white hover:bg-indigo-800 disabled:bg-indigo-300",
    secondary: "bg-white text-slate-900 ring-1 ring-slate-300 hover:bg-slate-50 disabled:text-slate-400",
    danger: "bg-white text-red-800 ring-1 ring-red-300 hover:bg-red-50 disabled:text-red-300",
  }[variant];
  return <button className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold
    focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600
    disabled:cursor-not-allowed ${styles} ${className}`} {...props} />;
}

export function Field({ label, id, hint, ...props }: React.InputHTMLAttributes<HTMLInputElement> & {
  label: string; id: string; hint?: string }) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium text-slate-800">{label}</label>
      <input id={id} aria-describedby={hint ? `${id}-hint` : undefined}
        className="block min-h-11 w-full rounded-lg border border-slate-300 px-3 py-2 text-base text-slate-900
          focus:border-indigo-600 focus:outline-none focus:ring-2 focus:ring-indigo-600/30" {...props} />
      {hint && <p id={`${id}-hint`} className="text-sm text-slate-600">{hint}</p>}
    </div>
  );
}

export function Alert({ kind = "error", children }: { kind?: "error" | "success" | "info"; children: React.ReactNode }) {
  const style = { error: "border-red-300 bg-red-50 text-red-900", success: "border-emerald-300 bg-emerald-50 text-emerald-900",
    info: "border-sky-300 bg-sky-50 text-sky-900" }[kind];
  return <div role={kind === "error" ? "alert" : "status"} className={`rounded-lg border px-4 py-3 text-sm ${style}`}>{children}</div>;
}

export function ProgressBar({ value, label }: { value: number; label: string }) {
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div>
      <div className="mb-1 flex justify-between text-sm text-slate-700"><span>{label}</span><span>{pct}%</span></div>
      <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
        className="h-3 w-full overflow-hidden rounded-full bg-slate-200">
        <div className="h-full rounded-full bg-indigo-600 transition-[width] duration-500" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function StatusBadge({ status }: { status: ProjectStatus }) {
  return <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ${STATUS_STYLE[status]}`}>
    {STATUS_LABEL[status]}</span>;
}

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-slate-200 bg-white p-5 shadow-sm ${className}`}>{children}</section>;
}
