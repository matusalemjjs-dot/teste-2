import { API_URL } from "./config";

/** Envia um erro do navegador para a API (só registro em log; sem dados pessoais nem consulta da URL). */
export function reportClientError(message: string, stack?: string, source = "window") {
  try {
    const body = JSON.stringify({ message: message.slice(0, 500), stack: (stack ?? "").slice(0, 2000),
      url: window.location.pathname, source });
    fetch(`${API_URL}/api/v1/client-errors`, { method: "POST", body, keepalive: true,
      headers: { "Content-Type": "application/json" } }).catch(() => undefined);
  } catch {
    /* nunca quebra a página por causa do relatório */
  }
}
