"use client";

import { useCallback } from "react";
import { Page } from "@/components/app-shell";
import { Alert, Card } from "@/components/ui";
import { api, type Policy } from "@/lib/api";
import { useApiData } from "@/lib/use-api";

export default function Privacidade() {
  const fetcher = useCallback(() => api<Policy>(null, "/api/v1/policy"), []);
  const { data: p, error } = useApiData(fetcher, null);
  return (
    <Page title="Como tratamos seus dados">
      {error && <Alert>{error.message}</Alert>}
      {!p ? !error && <p role="status">Carregando…</p> : (
        <div className="max-w-3xl space-y-5 text-slate-800">
          {!p.texto_juridico_revisado && <Alert kind="info">Versão do beta fechado. Este resumo descreve o
            funcionamento real do sistema; o texto jurídico definitivo ainda está em revisão.</Alert>}
          <Card>
            <h2 className="mb-2 text-lg font-semibold">O que fazemos com o seu vídeo</h2>
            <ul className="list-disc space-y-1 pl-5">
              <li>O vídeo é enviado direto para um armazenamento privado. Só você acessa, por links temporários.</li>
              <li>{p.transcricao_local ? "A fala é transcrita em nossos próprios servidores."
                : "A fala é transcrita por um serviço externo (listado abaixo)."}</li>
              <li>O texto transcrito é analisado por inteligência artificial (Claude, da Anthropic) para escolher
                os melhores momentos. O vídeo em si não é enviado para a análise.</li>
              <li>Metadados do arquivo (como localização) são descartados.</li>
            </ul>
          </Card>
          <Card>
            <h2 className="mb-2 text-lg font-semibold">Por quanto tempo guardamos</h2>
            <ul className="list-disc space-y-1 pl-5" data-testid="prazos">
              <li>Vídeo original, transcrição e trechos analisados: <strong>{p.retencao.video_original_dias} dias</strong> após
                a conclusão. Depois disso os cortes continuam disponíveis, mas não podem ser regenerados.</li>
              <li>Projeto completo, incluindo os cortes: <strong>{p.retencao.projeto_dias} dias</strong> após a conclusão
                (ou após a última atividade, se não foi concluído).</li>
              <li>Projetos criados sem vídeo: {p.retencao.projeto_sem_video_horas} horas.</li>
              <li>Registros técnicos de segurança, sem conteúdo dos vídeos: {p.retencao.auditoria_dias} dias.</li>
            </ul>
            <p className="mt-2 text-sm text-slate-600">A data exata de cada projeto aparece na página dele.</p>
          </Card>
          <Card>
            <h2 className="mb-2 text-lg font-semibold">Quem processa dados em nosso nome</h2>
            <ul className="list-disc space-y-1 pl-5">
              {p.suboperadores.map((s) => <li key={s.nome + s.finalidade}><strong>{s.nome}</strong>: {s.finalidade}</li>)}
            </ul>
          </Card>
          <Card>
            <h2 className="mb-2 text-lg font-semibold">Seus direitos</h2>
            <p>Você pode excluir projetos a qualquer momento e excluir sua conta, com todos os dados, na página
              <a href="/conta" className="mx-1 text-indigo-800 underline">Minha conta</a>.
              Para acessar ou corrigir seus dados, entre em contato:{" "}
              {p.contato ? <a className="text-indigo-800 underline" href={`mailto:${p.contato}`}>{p.contato}</a>
                : <span data-testid="contato-pendente">canal de contato ainda não configurado</span>}.</p>
          </Card>
        </div>
      )}
    </Page>
  );
}
