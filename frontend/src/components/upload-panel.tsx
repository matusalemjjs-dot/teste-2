"use client";

import { useState } from "react";
import { api, ApiError, uploadToStorage, type UploadTicket } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { bytes } from "@/lib/format";
import { Alert, Button, ProgressBar } from "./ui";

type Step = "idle" | "uploading" | "validating" | "starting";
const STEP_LABEL: Record<Step, string> = {
  idle: "", uploading: "Enviando o vídeo", validating: "Validando o vídeo (formato, duração e integridade)…",
  starting: "Iniciando o processamento…",
};

/** Envia o vídeo direto ao armazenamento, valida no servidor e inicia o processamento. */
export function UploadPanel({ projectId, onDone, onRefresh }: {
  projectId: string; onDone: () => void; onRefresh?: () => void }) {
  const { token } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [step, setStep] = useState<Step>("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    if (!file) return;
    setError(null);
    if (!file.name.toLowerCase().endsWith(".mp4")) return setError("Selecione um arquivo .mp4.");
    try {
      setStep("uploading");
      setProgress(0);
      const ticket = await api<UploadTicket>(token, `/api/v1/projects/${projectId}/upload-url`, {
        method: "POST",
        body: JSON.stringify({ filename: file.name, content_type: file.type || "video/mp4", size_bytes: file.size }),
      });
      await uploadToStorage(ticket, file, setProgress);
      setStep("validating");
      await api(token, `/api/v1/projects/${projectId}/finalize-upload`, { method: "POST" });
      setStep("starting");
      await api(token, `/api/v1/projects/${projectId}/process`, { method: "POST" });
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Erro inesperado. Tente novamente.");
      setStep("idle");
      onRefresh?.(); // atualiza o status (ex.: vídeo validado, mas sem cota para processar)
    }
  }

  const busy = step !== "idle";
  return (
    <div className="space-y-4">
      {error && <Alert>{error}</Alert>}
      <div className="space-y-1">
        <label htmlFor="arquivo" className="block text-sm font-medium text-slate-800">Vídeo (MP4, até 60 minutos)</label>
        <input id="arquivo" type="file" accept="video/mp4,.mp4" disabled={busy}
          onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(null); }}
          className="block w-full text-sm file:mr-4 file:min-h-11 file:rounded-lg file:border-0 file:bg-indigo-50
            file:px-4 file:font-semibold file:text-indigo-800 hover:file:bg-indigo-100" />
        {file && <p className="text-sm text-slate-600">{file.name} — {bytes(file.size)}</p>}
      </div>
      {step === "uploading" && <ProgressBar value={progress * 100} label={STEP_LABEL.uploading} />}
      {(step === "validating" || step === "starting") && <p role="status" className="text-sm text-slate-700">{STEP_LABEL[step]}</p>}
      <Button type="button" onClick={start} disabled={!file || busy}>{busy ? "Aguarde…" : "Enviar e processar"}</Button>
    </div>
  );
}
