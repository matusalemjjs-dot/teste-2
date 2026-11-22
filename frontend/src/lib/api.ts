import { API_URL } from "./config";

export type ProjectStatus = "pending" | "uploaded" | "queued" | "processing" | "completed" | "failed" | "canceled";

export type Project = {
  id: string; title: string; status: ProjectStatus; progress: number; status_message: string | null;
  error_code: string | null; source_duration_seconds: number | null; created_at: string; updated_at: string;
};
export type Job = { id: string; status: string; stage: string | null; attempts: number; error_code: string | null };
export type Retention = { source_available: boolean; source_expires_at: string | null; project_expires_at: string | null };
export type Policy = {
  retencao: { video_original_dias: number; projeto_dias: number; projeto_sem_video_horas: number; auditoria_dias: number };
  limites: { video_max_minutos: number; upload_max_mb: number };
  suboperadores: { nome: string; finalidade: string }[];
  contato: string | null; transcricao_local: boolean; texto_juridico_revisado: boolean;
};
export type ProjectStatusOut = {
  id: string; status: ProjectStatus; progress: number; status_message: string | null; error_code: string | null;
  updated_at: string; job: Job | null; retention: Retention | null;
};
export type Usage = {
  plan: string; quota_minutes: number; used_minutes: number; reserved_minutes: number; remaining_minutes: number;
  projects_last_24h: number; projects_per_day: number; period_end: string; quotas_enabled: boolean;
};
export type Me = { id: string; email: string | null; usage: Usage | null };
export type Clip = {
  id: string; project_id: string; title: string; justification: string | null; score: number;
  start_seconds: number; end_seconds: number; duration_seconds: number; status: string; layout: string;
  render_version: number; error_code: string | null; width: number | null; height: number | null;
  size_bytes: number | null; thumbnail_url: string | null; has_subtitles: boolean;
};
export type UploadTicket = { asset_id: string; url: string; fields: Record<string, string>; expires_at: string; max_bytes: number };
export type DownloadUrl = { url: string; expires_at: string; filename: string };

export class ApiError extends Error {
  constructor(public status: number, message: string, public code: string | null = null) {
    super(message);
  }
}

export async function api<T>(token: string | null, path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "Não foi possível conectar ao servidor. Verifique sua conexão.");
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    let message = typeof data?.detail === "string" ? data.detail : "Ocorreu um erro. Tente novamente.";
    if (Array.isArray(data?.erros) && data.erros[0]?.mensagem) message = data.erros[0].mensagem;
    if (res.status === 429 && !data?.detail) message = "Muitas requisições. Aguarde alguns segundos.";
    throw new ApiError(res.status, message, data?.code ?? null);
  }
  return data as T;
}

/** Envia o arquivo direto ao storage (POST pré-assinado) com progresso. */
export function uploadToStorage(ticket: UploadTicket, file: File, onProgress: (fraction: number) => void,
                                signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    Object.entries(ticket.fields).forEach(([k, v]) => form.append(k, v));
    form.append("file", file); // sempre por último
    const xhr = new XMLHttpRequest();
    xhr.open("POST", ticket.url);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve()
      : reject(new ApiError(xhr.status, "O envio do arquivo foi recusado pelo armazenamento.")));
    xhr.onerror = () => reject(new ApiError(0, "Falha de rede durante o envio. Tente novamente."));
    xhr.onabort = () => reject(new ApiError(0, "Envio cancelado."));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(form);
  });
}
