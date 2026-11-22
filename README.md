# AutoClip IA

Plataforma SaaS que transforma vídeos longos em cortes curtos usando transcrição, análise semântica por IA e
processamento com FFmpeg. Desenvolvida de forma incremental — veja [docs/ROADMAP.md](docs/ROADMAP.md) e
[docs/CHECKLIST.md](docs/CHECKLIST.md).

> **Estado atual: Etapa 8 (frontend).** Interface web completa em `frontend/`. O pipeline completo funciona: upload, validação,
> fila, transcrição, análise por IA, renderização vertical com legendas, miniaturas, download e regeneração.
> Sem `ANTHROPIC_API_KEY`, a análise falha com `analysis_misconfigured` e nenhum corte é gerado.
> Nada neste repositório simula resultados de processamento.

## Implantação (beta)

Produção em uma VM com Docker Compose, HTTPS automático e armazenamento gerenciado: veja
[docs/IMPLANTACAO.md](docs/IMPLANTACAO.md) (`deploy/`). Validação das integrações reais, checklist de liberação e
acompanhamento do beta: [docs/BETA.md](docs/BETA.md) (`python -m app.validar`).

## Estrutura

```
frontend/           Next.js 16 + React 19 + TypeScript + Tailwind (interface em português)
  src/app/          páginas: entrar, cadastro, recuperar/redefinir senha, painel, novo projeto, projeto/galeria
  src/lib/          autenticação (Supabase), cliente da API, formatação
  e2e/              testes Playwright contra a pilha real
backend/            API FastAPI (Python 3.12+)
  app/core/         configuração, logs estruturados, validação de JWT
  app/models/       modelo de dados SQLAlchemy (10 tabelas)
  app/api/v1/       rotas
  app/services/     regras de negócio (projetos, upload, storage, fila, auditoria)
  app/pipeline/     etapas do processamento e contexto (progresso, cancelamento, subprocessos)
  app/worker/       worker RQ, limites de recursos, limpeza de temporários
  app/transcription/ adaptadores de transcrição (faster-whisper local, serviço externo)
  app/analysis/     janelas candidatas, adaptador do modelo de linguagem, nota e seleção
  app/rendering/    legendas (ASS/SRT), comandos FFmpeg, publicação dos cortes
  alembic/          migrações
  tests/            Pytest (usa PostgreSQL real)
infra/              scripts de infraestrutura local
docker-compose.yml  PostgreSQL 16, Redis 7 e MinIO (S3 local)
```

## API disponível nesta etapa

Documentação OpenAPI interativa em `http://localhost:8000/docs` (JSON em `/openapi.json`).

| Método | Rota | Descrição |
|---|---|---|
| GET | `/health` | Saúde da API e do banco |
| GET | `/api/v1/me` | Usuário autenticado e consumo do mês (cria perfil e assinatura no primeiro acesso) |
| POST | `/api/v1/projects` | Criar projeto (`title`, `rights_confirmed: true`) |
| GET | `/api/v1/projects` | Listar (`limit`, `offset`, `status`) |
| GET | `/api/v1/projects/{id}` | Consultar |
| GET | `/api/v1/projects/{id}/status` | Status e progresso |
| POST | `/api/v1/projects/{id}/upload-url` | URL assinada (POST multipart) para enviar o MP4 direto ao storage |
| POST | `/api/v1/projects/{id}/finalize-upload` | Valida o arquivo enviado com FFprobe (idempotente) |
| GET | `/api/v1/projects/{id}/transcript` | Segmentos transcritos com timestamps |
| GET | `/api/v1/projects/{id}/candidates` | Trechos avaliados pela IA (`?only_selected=true`) |
| GET | `/api/v1/projects/{id}/clips` | Cortes do projeto, com miniatura (URL temporária) |
| GET | `/api/v1/clips/{id}` | Consultar corte |
| POST | `/api/v1/clips/{id}/download-url` | URL temporária: `{"disposition": "attachment"\|"inline", "file": "video"\|"subtitle"}` |
| POST | `/api/v1/clips/{id}/regenerate` | Regenerar com `{"layout": "center_crop"\|"blur_background", "subtitles": true}` |
| POST | `/api/v1/projects/{id}/process` | Enfileirar processamento (idempotente) |
| POST | `/api/v1/projects/{id}/cancel` | Cancelar, inclusive durante o processamento (idempotente) |
| DELETE | `/api/v1/projects/{id}` | Excluir projeto **e seus arquivos** (bloqueado durante processamento) |

### Fluxo de upload

1. `POST /projects/{id}/upload-url` com `{"filename": "aula.mp4", "content_type": "video/mp4", "size_bytes": 123}`.
   Pedidos com extensão, tipo ou tamanho inválidos são recusados (422, com `code` e mensagem em português).
2. O cliente envia um `multipart/form-data` para `url`, com todos os `fields` recebidos e o arquivo no campo
   `file` (por último). A URL expira em `UPLOAD_URL_EXPIRES_SECONDS`. A política assinada limita tamanho e tipo.
3. `POST /projects/{id}/finalize-upload`. O servidor:
   - confere o tamanho real no storage;
   - confere a assinatura binária MP4 (`ftyp` + marca compatível; QuickTime/MKV/WebM renomeados são recusados);
   - roda o FFprobe restrito ao demuxer MP4, lendo o arquivo por URL assinada interna (sem baixar tudo);
   - exige faixa de vídeo e de áudio e duração entre `MIN_VIDEO_SECONDS` e `MAX_VIDEO_MINUTES`;
   - decodifica o início e o fim do vídeo para detectar arquivo corrompido ou truncado.

   A validação é feita sobre uma **cópia imutável** (`{asset}.validated.mp4`), criada antes da análise; a URL de
   upload só alcança a chave original, então o arquivo validado não pode ser trocado depois. Se válido, o
   projeto passa para `uploaded` e a chave original é apagada. Se inválido, o arquivo é **apagado**, o projeto continua em
   `pending` com `error_code`/`status_message` e o usuário pode tentar de novo.

Códigos de erro: `invalid_extension`, `invalid_type`, `empty_file`, `file_too_large`, `too_short`, `too_long`,
`no_video_stream`, `no_audio_stream`, `corrupted`, `probe_timeout`, `upload_not_requested`, `upload_missing`,
`invalid_state`.

### Processamento

- `POST /process` só aceita projetos `uploaded` (ou `failed`, para nova tentativa). Repetir a chamada com o
  projeto em fila, processando ou concluído devolve o **mesmo** job (HTTP 200, `created=false`).
- Cada tentativa é um registro em `processing_jobs` com chave única `process_project:{projeto}:{n}`. O worker só
  executa jobs `queued`; reexecutar a tarefa não processa de novo.
- Limite de `MAX_PROCESS_ATTEMPTS` tentativas que falharam por causa do vídeo/processamento. Falhas de
  infraestrutura (`queue_unavailable`, `worker_lost`, `insufficient_disk`, `storage_error`, `stage_unavailable`,
  `transcription_unavailable`, `transcription_misconfigured`, `analysis_unavailable`, `analysis_misconfigured`)
  não contam.
- `GET /status` mostra `progress`, `status_message` e o job atual (`stage`, `attempts`, `error_code`).
- Cancelar marca o projeto como `canceled`; se o job ainda está na fila ele é removido, se está rodando o
  worker para no próximo ponto de verificação (subprocessos como o FFmpeg são encerrados).

Etapas do pipeline (`app/pipeline/stages.py`):

| Etapa | Estado |
|---|---|
| `prepare` — baixa o vídeo, confere tamanho e grava o SHA-256 | implementada |
| `transcribe` — extrai o áudio (FFmpeg), transcreve e grava `transcript_segments` | implementada |
| `analyze` — janelas de 15–60 s, avaliação pelo Claude, validação e seleção de até 10 trechos | implementada |
| `render` — cortes 1080x1920 (H.264/AAC), legendas queimadas + SRT, miniatura, publicação no storage | implementada |

### Transcrição

Provedor escolhido por `TRANSCRIPTION_PROVIDER`:

- `faster_whisper` (padrão): roda no worker, sem custo por minuto. O modelo (`WHISPER_MODEL`) é baixado do
  Hugging Face na primeira execução; para baixar antes: `python -m app.transcription.download`.
  O worker precisa acessar `huggingface.co` (e a CDN dele) só nessa primeira vez.
- `openai`: envia MP3 mono 32 kbps para `POST {OPENAI_BASE_URL}/audio/transcriptions` (`verbose_json`, com
  timestamps de segmento e palavra). Exige `OPENAI_API_KEY`. Limite de 25 MB (~100 min nesse formato).

Os segmentos são normalizados antes de gravar: dentro de `[0, duração]`, em ordem, sem sobreposição e sem texto
vazio. Reprocessar substitui a transcrição anterior. Vídeo sem fala → `no_speech`.

### Análise inteligente

1. **Janelas candidatas** (`app/analysis/windows.py`): começam e terminam em limites de segmento da
   transcrição, de preferência após/antes de fim de frase, com durações-alvo de ~22, ~35 e ~50 s, sempre entre
   `CLIP_MIN_SECONDS` e `CLIP_MAX_SECONDS`. Quase-duplicatas (>85% de sobreposição) são removidas; no máximo
   `LLM_MAX_WINDOWS` janelas, distribuídas pelo vídeo todo.
2. **Avaliação pelo Claude** (`app/analysis/llm.py`), em lotes de `LLM_WINDOWS_PER_REQUEST`, com resposta em
   JSON Schema (`output_config.format`): notas 0–10 para gancho, contexto, emoção, relevância e retenção, título
   e justificativa em português. A fala transcrita é tratada como dado (delimitada e com instrução para ignorar
   comandos embutidos). Com `LLM_FALLBACKS=default`, se o modelo recusar o conteúdo, o servidor da Anthropic
   tenta outro modelo automaticamente (recurso beta `server-side-fallback-2026-07-01`).
3. **Qualidade técnica** é calculada pelo servidor a partir da transcrição (proporção de fala e confiança do
   reconhecimento) — análise visual está fora do MVP.
4. **Nota final 0–100** = média ponderada (gancho 25%, retenção 20%, relevância 20%, emoção 15%,
   contexto 10%, técnica 10%).
5. **Validação e seleção**: itens com id inexistente/duplicado, notas fora de 0–10 ou texto vazio são
   descartados; timestamps e duração são conferidos; seleção gulosa por nota **sem sobreposição**, até
   `MAX_CLIPS`. Todos os avaliados ficam em `clip_candidates` (`selected=true` nos escolhidos).
6. **Consumo**: tokens e custo estimado de cada job ficam em `usage_ledger` (`llm_tokens`, chave idempotente).

Custo estimado com `claude-opus-5-5` (US$ 4 / US$ 20 por milhão de tokens de entrada/saída): da ordem de
US$ 0,30–0,80 por hora de vídeo com 200 janelas — **estimativa, não medida** (veja o checklist).

### Renderização

- **Formato**: MP4 1080x1920 (`RENDER_WIDTH`/`RENDER_HEIGHT`), H.264 `yuv420p` + AAC, `faststart` (reprodução
  antes do download completo), sem metadados do arquivo original. O áudio é o original do vídeo.
- **Layouts** (a proporção nunca é distorcida):
  - `center_crop` (padrão): amplia até preencher a tela e corta as laterais, centralizado;
  - `blur_background`: o quadro inteiro fica no centro, sobre uma cópia ampliada e desfocada.
- **Legendas**: montadas a partir dos timestamps por palavra (até 4 palavras ou 1,8 s por legenda, quebra em
  pontuação), queimadas no vídeo (ASS/libass, fonte `SUBTITLE_FONT`) e disponíveis em SRT.
- Cada corte é verificado com FFprobe (resolução, duração, faixas) antes de ser publicado.
- Um corte com falha não impede os demais (`"3 de 4 cortes gerados; os demais falharam"`); se todos falharem,
  o projeto termina com `render_failed`.
- **Regenerar** cria uma nova versão (`render_version`); a anterior continua disponível até a nova ficar pronta
  e é apagada depois. Se a regeneração falhar, a versão anterior é mantida.
- **Downloads**: URLs assinadas emitidas só para o dono e só com o corte pronto; expiram em
  `DOWNLOAD_URL_EXPIRES_SECONDS` (padrão 5 min). Cada emissão gera um evento de auditoria.
- Projeto com regeneração **rodando** não pode ser excluído (409) até terminar; regeneração apenas na fila é
  cancelada pela exclusão.

### Consumo, cotas e limite de requisições

- **Minutos processados**: debitados em `usage_ledger` (`processed_seconds`) somente quando o projeto conclui,
  com chave única por projeto — falhas não são cobradas e repetições não cobram de novo.
- **Cota mensal** (`subscriptions.monthly_minutes_quota`, mês civil em UTC): ao pedir processamento, conta-se o
  consumo do mês + projetos em fila/processando + o vídeo atual. Excedeu → **402** `quota_exceeded`. A linha
  da assinatura é travada durante a verificação para que pedidos simultâneos não estourem a cota.
- **Projetos por dia** (`max_projects_per_day`, últimas 24 h): contados pela auditoria, então excluir projetos
  não libera a cota. Excedeu → **429** `project_limit`.
- **Conta suspensa** (`subscriptions.status != active`) → **403** `subscription_inactive`.
- **Limite de requisições** por usuário (Redis, janela fixa): geral (`RATE_LIMIT_GENERAL`) e para operações que
  geram custo (`RATE_LIMIT_EXPENSIVE`). Excedeu → **429** com `Retry-After`. Se o Redis cair, o limite é
  ignorado (registrado em log) e as cotas no banco continuam valendo.
- **Custos internos estimados** em `usage_ledger.estimated_cost_usd`: LLM por tokens reais × preço configurado;
  processamento e renderização por minuto (`COST_PER_*_MINUTE_USD`, padrão 0 — defina com seus custos reais).
- `GET /me` → `usage`: `quota_minutes`, `used_minutes`, `reserved_minutes`, `remaining_minutes`,
  `projects_last_24h`, `projects_per_day`.

Os valores de plano em `PLAN_FREE_*` valem para novas assinaturas; para mudar a cota de um usuário existente,
altere a linha em `subscriptions` (o painel administrativo vem na Etapa 9). **Antes de abrir acesso público,
revise esses limites.**

Erros do worker: `render_failed`, `no_candidates`, `analysis_refused`, `analysis_failed`, `analysis_unavailable`,
`analysis_misconfigured`, `no_speech`, `audio_extraction_failed`, `transcription_failed`, `transcription_unavailable`,
`transcription_misconfigured`, `stage_unavailable`, `source_missing`, `source_changed`, `storage_error`, `insufficient_disk`,
`stage_timeout`, `timeout`, `resource_limit`, `worker_lost`, `internal_error` (mensagem genérica ao usuário;
detalhe técnico, sem segredos, em `processing_jobs.error_detail`).

**Recuperação de jobs** — `python -m app.worker.maintenance` (uma rodada) ou `--loop` (a cada
`MAINTENANCE_INTERVAL_SECONDS`, padrão 5 min); também roda quando o worker inicia. Uma trava do Postgres impede
duas rodadas ao mesmo tempo. A cada rodada:
- jobs "na fila" no banco que sumiram do Redis (ex.: Redis reiniciado) voltam para a fila com o mesmo id —
  o worker só executa jobs ainda "na fila", então nunca processa nem cobra duas vezes; depois de
  `MAX_REQUEUES` devoluções o job falha com `queue_lost` (não consome tentativa);
- jobs "rodando" sem sinal de vida há `STALE_HEARTBEAT_SECONDS` (padrão 10 min) são marcados `worker_lost`.
  O sinal de vida é uma thread do processo do job que registra atividade a cada `HEARTBEAT_INTERVAL_SECONDS`;
- diretórios temporários órfãos são apagados.

**Retenção (LGPD)** — `python -m app.worker.maintenance --loop` também executa a retenção a cada
`RETENTION_INTERVAL_HOURS` (ou na hora, com `--retencao`). Prazos padrão: vídeo original, transcrição e trechos
candidatos 7 dias após a conclusão (os cortes continuam, mas não podem ser regenerados); projeto inteiro 30 dias
após a conclusão (ou a última atividade); projeto sem vídeo 24 h; arquivos sem registro no banco 24 h; detalhe
técnico de erros 30 dias; auditoria 365 dias. Apaga primeiro do storage e depois do banco; projetos com job ativo
ou travados são pulados e tentados na rodada seguinte. As datas aparecem na página de cada projeto e os prazos
em `/privacidade` (lidos da API pública `GET /api/v1/policy`).

**Exclusão de conta** — `DELETE /api/v1/me` (página "Minha conta"): apaga todos os arquivos e dados do usuário,
mantém o registro de consumo sem vínculo pessoal, impede que um token antigo recrie a conta e remove o login no
Supabase quando `SUPABASE_SERVICE_ROLE_KEY` está configurada no backend (sem ela, a resposta avisa que o login
deve ser removido no painel do Supabase). Com processamento rodando, responde 409.

### Beta fechado

- **Convites**: desligue o cadastro público no Supabase (Authentication > Providers > Email: desativar
  "Allow new users to sign up") e use `BETA_MODE=true` na API. Só e-mails em `BETA_ALLOWED_EMAILS` ou convidados
  pelo comando de administração passam (403 para os demais, verificado em toda requisição). No frontend,
  `NEXT_PUBLIC_BETA_MODE=true` troca o cadastro por "acesso por convite". O link de convite do Supabase deve
  levar a `/redefinir-senha` (Redirect URLs), onde a pessoa cria a senha.
- **Limites** (configuráveis): cota de minutos no período (`BETA_PERIOD_*` ou período por usuário), 1
  processamento por vez, 20 regenerações por dia, projetos por dia.
- **Custo de IA com reserva prévia**: ao enfileirar, reserva-se `LLM_RESERVE_USD_PER_MINUTE` × minutos do vídeo;
  o pedido é recusado se gasto real + reservas + este vídeo passar do teto do usuário (`LLM_BUDGET_PER_USER_USD`,
  402 `cost_limit`) ou do beta (`LLM_BUDGET_TOTAL_USD`, 402 `beta_budget_exhausted`; verificação serializada
  por trava do Postgres). Quando a análise termina, vale o custo real medido pelos tokens.
- **Administração** (somente no servidor, via SSH com chave; não há rota web de administração):

```bash
python -m app.admin convidar email@dominio.com [--enviar-email]   # --enviar-email exige SUPABASE_SERVICE_ROLE_KEY
python -m app.admin revogar email@dominio.com
python -m app.admin convites | usuarios
python -m app.admin suspender email@dominio.com | reativar email@dominio.com
python -m app.admin cota email@dominio.com --minutos 180 --projetos-dia 5
python -m app.admin periodo --todos --inicio 2026-12-01 --fim 2026-12-14
python -m app.admin relatorio [--horas 24] [--json]
```

Cada comando que altera algo é registrado na auditoria (`admin.*`, com o usuário do sistema).

### Monitoramento

- `GET /health` (processo vivo) e `GET /health/ready` (banco, Redis, armazenamento, worker vivo e fila; 503 se
  algo essencial falhar) — aponte o monitor de disponibilidade externo para `/health/ready`.
- **Alertas** (`ALERT_WEBHOOK_URL`, formatos `slack`, `discord` ou `json`), avaliados pela manutenção a cada
  5 min: falhas acima de `ALERT_FAILURES_PER_HOUR`, espera na fila acima de `ALERT_QUEUE_WAIT_MINUTES`, nenhum
  worker vivo, Redis inacessível, custo de IA acima de `ALERT_BUDGET_FRACTION` do teto, jobs perdidos, falha na
  retenção ou na própria manutenção. Todo alerta também vai para o log (ERROR); o mesmo alerta não se repete
  antes de `ALERT_REPEAT_MINUTES`. Sem dados pessoais.
- **Relatório diário** no mesmo webhook (o mesmo de `python -m app.admin relatorio`), incluindo as avaliações dos
  cortes ("útil"/"não útil" e a nota estimada média de cada grupo).
- **Erros do navegador**: erros de JavaScript e relatórios de CSP vão para `POST /api/v1/client-errors` e
  `/api/v1/csp-report` (só log, sem consulta da URL nem tokens; limite por IP).
- **Cabeçalhos**: o frontend envia `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`
  e a CSP **em modo só observação** (`Content-Security-Policy-Report-Only`); defina `NEXT_PUBLIC_STORAGE_ORIGIN`
  com a origem do armazenamento. A API envia `nosniff`, `no-referrer`, `Cache-Control: no-store` e, com
  `ENVIRONMENT=production`, desliga `/docs`, `/redoc` e `/openapi.json`.
- **Modo de login de teste**: o build do frontend falha com `NEXT_PUBLIC_AUTH_MODE=test`, a menos que
  `AUTOCLIP_ALLOW_TEST_AUTH=1` (usado só pelos testes E2E).

**Exclusão de projeto** — trava o projeto; jobs ainda na fila (ex.: regeneração presa) são cancelados e o
projeto é excluído; com job rodando, a resposta é 409 até ele terminar ou ser recuperado.

Limites do worker: tempo total por job (`JOB_TIMEOUT_SECONDS`), tempo por subprocesso, memória (`RLIMIT_AS`),
CPU (`RLIMIT_CPU`) e tamanho máximo de arquivo gerado (`RLIMIT_FSIZE`) aplicados ao processo de cada job
(Linux/macOS), espaço livre mínimo em disco, diretório temporário por job apagado ao final e limpeza de órfãos
na inicialização. Jobs `running` sem atualização além do timeout são marcados como `worker_lost`.

Exemplo de envio no navegador (Etapa 8 usará isto):

```js
const form = new FormData();
Object.entries(ticket.fields).forEach(([k, v]) => form.append(k, v));
form.append("file", arquivo); // sempre por último
await fetch(ticket.url, { method: "POST", body: form });
```

Todas as rotas `/api/v1/*` exigem `Authorization: Bearer <access_token do Supabase>`. Projetos de outros
usuários retornam 404 (indistinguíveis de inexistentes).

> **Atenção (22/11/2026)**: a imagem `minio/minio` usada no `docker-compose.yml` de **desenvolvimento** não está
> mais disponível no Docker Hub (a MinIO deixou de distribuir imagens gratuitas). A produção não é afetada (usa
> S3/R2). Até a substituição ser aprovada, use um bucket S3/R2 de desenvolvimento ou o emulador usado nos testes
> (`python -m moto.server -p 9000`, instalado com `requirements-dev.txt`).

## Executar no Windows

Pré-requisitos: [Python 3.12+](https://www.python.org/downloads/) (marque "Add to PATH"),
[Docker Desktop](https://www.docker.com/products/docker-desktop/), Git e FFmpeg
(`winget install Gyan.FFmpeg`; feche e reabra o terminal e confira com `ffprobe -version`).

No PowerShell, na pasta do repositório:

```powershell
# 1. Infraestrutura (PostgreSQL + Redis + MinIO; o bucket privado é criado automaticamente)
docker compose up -d

# 2. Ambiente Python
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1        # se bloqueado: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements-dev.txt

# 3. Configuração
Copy-Item ..\.env.example .env      # edite SUPABASE_URL (e SUPABASE_JWT_SECRET se usar HS256)

# 4. Migrações
alembic upgrade head

# 5. API
uvicorn app.main:app --reload --port 8000
```

### Frontend no Windows

Pré-requisito: [Node.js 22 LTS](https://nodejs.org/). Em outro terminal PowerShell:

```powershell
cd frontend
npm install
Copy-Item .env.example .env.local   # preencha NEXT_PUBLIC_SUPABASE_URL e NEXT_PUBLIC_SUPABASE_ANON_KEY
npm run dev                         # http://localhost:3000
```

Configuração no painel do Supabase (Authentication > URL Configuration): **Site URL** `http://localhost:3000` e
**Redirect URLs** `http://localhost:3000/entrar` e `http://localhost:3000/redefinir-senha`. No backend,
`SUPABASE_URL` deve ser o mesmo projeto.

O navegador envia o vídeo direto ao storage, então o bucket precisa aceitar a origem do frontend (CORS):
- MinIO do `docker compose`: já liberado para `http://localhost:3000` (`MINIO_API_CORS_ALLOW_ORIGIN`);
- AWS S3 / R2 / outros: defina `S3_CORS_ORIGINS` e rode `python -m app.storage_setup` no backend.

### Worker no Windows

O RQ depende de `fork()`, que não existe no Windows. Opções:

1. **Recomendado:** worker em contêiner: `docker compose --profile worker up -d --build worker`
   (usa `backend/.env` e aplica limites de CPU/memória do Docker).
2. **WSL2 (Ubuntu):** siga as instruções de Linux dentro do WSL e rode `python -m app.worker.run`.
3. Nativo: `python -m app.worker.run` usa um worker sem fork, com timeout por temporizador e **sem** limites
   de memória/CPU. Use só para testes locais — esse modo não foi testado em Windows.

### Testes de ponta a ponta (Playwright)

```bash
cd frontend && npm run test:e2e
```

O Playwright sobe sozinho a pilha completa (`backend/tests/e2e/stack.py`): S3 emulado com CORS, Redis, banco
`autoclip_e2e` recém-migrado, **worker real** (FFmpeg, Whisper `tiny`) e API, além do frontend compilado em
modo de teste de autenticação (token HS256 assinado pelo próprio teste; aparece a faixa "Modo de teste").
A única peça substituída é o avaliador de IA (o worker E2E usa o avaliador de teste, definido em
`backend/tests/e2e/worker.py`, fora do código do produto). Requer o backend instalado (`backend/.venv`),
PostgreSQL acessível com o usuário `autoclip`, `redis-server`, `ffmpeg` e `espeak-ng` — no Windows, rode no WSL2.

### Caminhos protegidos

`scripts/check_protected_paths.sh <referência>` falha se `backend/app/analysis`, `backend/app/transcription`,
`backend/app/rendering` ou `backend/app/services/media_probe.py` mudaram desde a referência. Alterações
nesses caminhos (seleção de melhores momentos, transcrição, renderização, validação de mídia) exigem
aprovação explícita.

### Testes do backend

Os testes usam o banco `autoclip_test` (criado automaticamente pelo `docker compose` na primeira subida), um
emulador S3 local (moto, iniciado pelos próprios testes), um `redis-server` iniciado pelos testes e vídeos
reais gerados com FFmpeg — portanto `ffmpeg` e `redis-server` precisam estar no PATH. Os testes de transcrição
real usam fala sintética do `espeak-ng` (só como dado de teste) e o modelo Whisper `tiny`; sem `espeak-ng` ou
sem o modelo, esses testes são **pulados** (veja com `pytest -rs`). O teste `tests/test_llm_real.py` chama a API
real da Anthropic e só roda com `ANTHROPIC_API_KEY` definida (custo aproximado de US$ 0,01); os demais testes da
análise usam um avaliador de teste determinístico e um servidor HTTP local que imita a API. No Windows, rode os
testes dentro do WSL2 (o worker com fork e os limites de recursos só existem em Linux/macOS):

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest
ruff check app tests
```

Para apontar para outro banco: `$env:DATABASE_URL="postgresql+psycopg://usuario:senha@host:5432/banco"`.

> Se o volume do Postgres já existia antes do `init.sql`, crie o banco de testes manualmente:
> `docker compose exec postgres createdb -U autoclip autoclip_test`

## Executar no Linux/macOS

```bash
docker compose up -d
cd backend && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example .env
alembic upgrade head && uvicorn app.main:app --reload
python -m app.worker.run          # em outro terminal
pytest
```

## Autenticação (Supabase)

O cadastro, login, logout e recuperação de senha são feitos pelo Supabase Auth no frontend (Etapa 8). O
backend só **valida** o access token:

- Projetos com chaves de assinatura assimétricas (ES256/RS256): basta `SUPABASE_URL`; a chave pública é obtida
  do JWKS em `/auth/v1/.well-known/jwks.json`.
- Projetos com segredo legado HS256: defina também `SUPABASE_JWT_SECRET`.
- São verificados assinatura, expiração, `aud=authenticated`, `iss=<SUPABASE_URL>/auth/v1` e `role`.
  Tokens `service_role` são recusados.

Gerar um token para testar manualmente: faça login com `supabase-js` (`supabase.auth.signInWithPassword`) e use
`session.access_token`.

## Storage

- Bucket **privado** (`mc anonymous set none`). Arquivos só são acessados por URLs assinadas e temporárias.
- Chaves geradas pelo servidor: `users/{owner_id}/projects/{project_id}/source/{asset_id}.mp4`. O nome enviado
  pelo usuário nunca entra no caminho.
- Para o envio direto pelo navegador, o storage precisa permitir CORS de `http://localhost:3000` (no AWS S3/R2,
  configure a regra CORS do bucket para `POST`). Isso será validado na Etapa 8.
- Em produção, use credenciais restritas ao bucket.

## Segurança e privacidade

- Segredos apenas em variáveis de ambiente (`.env` está no `.gitignore`).
- Logs em JSON com remoção de tokens, senhas e chaves.
- Toda consulta de recurso filtra por `owner_id`.
- A criação de projeto exige `rights_confirmed: true` (o usuário declara ter autorização sobre o conteúdo).
- Metadados livres do vídeo (título, localização GPS etc.) **não** são armazenados; só dados técnicos.
- Excluir um projeto apaga primeiro os arquivos do storage; se o storage falhar, nada é apagado e a API retorna 503.
- Excluir um perfil remove em cascata projetos, mídias, transcrições e cortes; registros de uso e auditoria são
  mantidos com o vínculo anulado.
