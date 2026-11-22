"use client";

import { useCallback, useState } from "react";
import { api, ApiError, type Clip, type DownloadUrl } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { duration } from "@/lib/format";
import { useApiData } from "@/lib/use-api";
import { Alert, Button } from "./ui";

function ClipCard({ clip, onChanged }: { clip: Clip; onChanged: () => void }) {
  const { token } = useAuth();
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [layout, setLayout] = useState(clip.layout === "blur_background" ? "blur_background" : "center_crop");
  const [subtitles, setSubtitles] = useState(true);
  const [showOptions, setShowOptions] = useState(false);
  const busy = clip.status === "pending" || clip.status === "rendering";

  async function url(file: "video" | "subtitle", disposition: "attachment" | "inline") {
    return api<DownloadUrl>(token, `/api/v1/clips/${clip.id}/download-url`, {
      method: "POST", body: JSON.stringify({ file, disposition }),
    });
  }
  async function run(action: () => Promise<void>) {
    setError(null);
    try { await action(); } catch (e) { setError(e instanceof ApiError ? e.message : "Erro inesperado."); }
  }
  const play = () => run(async () => setVideoUrl((await url("video", "inline")).url));
  const download = (file: "video" | "subtitle") => run(async () => {
    const d = await url(file, "attachment");
    window.location.assign(d.url);
  });
  const regenerate = () => run(async () => {
    await api(token, `/api/v1/clips/${clip.id}/regenerate`, { method: "POST", body: JSON.stringify({ layout, subtitles }) });
    setShowOptions(false);
    setVideoUrl(null);
    onChanged();
  });

  return (
    <li className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm" data-testid="clip">
      <div className="relative aspect-[9/16] max-h-96 bg-slate-900">
        {videoUrl ? <video src={videoUrl} controls autoPlay playsInline className="h-full w-full object-contain"
          aria-label={`Vídeo: ${clip.title}`} />
          : clip.thumbnail_url ? (
            // eslint-disable-next-line @next/next/no-img-element -- URL temporária do storage
            <img src={clip.thumbnail_url} alt={`Miniatura do corte: ${clip.title}`} className="h-full w-full object-cover" />
          ) : <div className="flex h-full items-center justify-center text-sm text-slate-300">Sem miniatura</div>}
        <span className="absolute left-2 top-2 rounded-full bg-black/75 px-2 py-0.5 text-xs font-bold text-white">
          Nota {clip.score}/100</span>
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <h3 className="font-semibold leading-snug">{clip.title}</h3>
        <p className="text-sm text-slate-600">{duration(clip.duration_seconds)}
          {clip.layout === "blur_background" ? " · fundo desfocado" : " · enquadramento central"}</p>
        {clip.justification && <p className="text-sm text-slate-800">{clip.justification}</p>}
        {busy && <p role="status" className="text-sm font-medium text-indigo-800">Gerando nova versão…</p>}
        {clip.status === "failed" && <Alert>Não foi possível gerar este corte.</Alert>}
        {clip.error_code === "render_failed" && clip.status === "ready" &&
          <Alert kind="info">A última regeneração falhou; esta é a versão anterior.</Alert>}
        {error && <Alert>{error}</Alert>}
        <div className="mt-auto flex flex-wrap gap-2 pt-2">
          <Button onClick={play} disabled={clip.status !== "ready"}>Assistir</Button>
          <Button variant="secondary" onClick={() => download("video")} disabled={clip.status !== "ready"}>Baixar MP4</Button>
          {clip.has_subtitles && <Button variant="secondary" onClick={() => download("subtitle")}
            disabled={clip.status !== "ready"}>Legenda (SRT)</Button>}
          <Button variant="secondary" onClick={() => setShowOptions((v) => !v)} disabled={busy}
            aria-expanded={showOptions}>Regenerar</Button>
        </div>
        {showOptions && (
          <fieldset className="mt-2 space-y-3 rounded-lg border border-slate-200 p-3">
            <legend className="px-1 text-sm font-semibold">Regenerar corte</legend>
            <div className="space-y-1">
              <label htmlFor={`layout-${clip.id}`} className="block text-sm font-medium">Enquadramento</label>
              <select id={`layout-${clip.id}`} value={layout} onChange={(e) => setLayout(e.target.value)}
                className="min-h-11 w-full rounded-lg border border-slate-300 px-3">
                <option value="center_crop">Centralizado (preenche a tela)</option>
                <option value="blur_background">Vídeo inteiro com fundo desfocado</option>
              </select>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={subtitles} onChange={(e) => setSubtitles(e.target.checked)}
                className="h-5 w-5 accent-indigo-700" /> Legendas no vídeo</label>
            <Button onClick={regenerate}>Gerar nova versão</Button>
          </fieldset>
        )}
      </div>
    </li>
  );
}

export function Gallery({ projectId }: { projectId: string }) {
  const { token } = useAuth();
  const [fast, setFast] = useState(false);
  const fetcher = useCallback(
    () => api<{ items: Clip[] }>(token, `/api/v1/projects/${projectId}/clips`).then((r) => r.items),
    [token, projectId]);
  // rápido durante regenerações; lento no restante para renovar as URLs temporárias das miniaturas
  const { data: clips, error: apiError, reload } = useApiData(fetcher, fast ? 3000 : 240_000);
  const rendering = !!clips?.some((c) => c.status === "pending" || c.status === "rendering");
  if (rendering !== fast) setFast(rendering);
  const error = apiError?.message ?? null;
  const load = reload;

  if (error) return <Alert>{error}</Alert>;
  if (!clips) return <p role="status" className="text-slate-600">Carregando cortes…</p>;
  if (!clips.length) return <p className="text-slate-700">Nenhum corte gerado.</p>;
  return (
    <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3" aria-label="Cortes gerados">
      {clips.map((c) => <ClipCard key={`${c.id}-${c.render_version}-${c.status}`} clip={c} onChanged={load} />)}
    </ul>
  );
}
