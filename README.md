# AutoClip IA

Plataforma SaaS que transforma vídeos longos em cortes curtos usando transcrição, análise semântica por IA e
processamento com FFmpeg. Desenvolvida de forma incremental — veja [docs/ROADMAP.md](docs/ROADMAP.md) e
[docs/CHECKLIST.md](docs/CHECKLIST.md).

> **Estado atual: Etapa 5 (análise inteligente).** Upload, validação, fila, worker, transcrição e análise por IA
> funcionam. A renderização **ainda não existe**: o projeto termina como `failed` com
> `error_code=stage_unavailable` na etapa `render`, com transcrição e candidatos salvos
> (`GET /projects/{id}/transcript` e `/candidates`). Sem `ANTHROPIC_API_KEY`, a análise falha com
> `analysis_misconfigured`. Nada neste repositório simula resultados de processamento.

## Estrutura

```
backend/            API FastAPI (Python 3.12+)
  app/core/         configuração, logs estruturados, validação de JWT
  app/models/       modelo de dados SQLAlchemy (10 tabelas)
  app/api/v1/       rotas
  app/services/     regras de negócio (projetos, upload, storage, fila, auditoria)
  app/pipeline/     etapas do processamento e contexto (progresso, cancelamento, subprocessos)
  app/worker/       worker RQ, limites de recursos, limpeza de temporários
  app/transcription/ adaptadores de transcrição (faster-whisper local, serviço externo)
  app/analysis/     janelas candidatas, adaptador do modelo de linguagem, nota e seleção
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
| GET | `/api/v1/me` | Usuário autenticado (cria o perfil no primeiro acesso) |
| POST | `/api/v1/projects` | Criar projeto (`title`, `rights_confirmed: true`) |
| GET | `/api/v1/projects` | Listar (`limit`, `offset`, `status`) |
| GET | `/api/v1/projects/{id}` | Consultar |
| GET | `/api/v1/projects/{id}/status` | Status e progresso |
| POST | `/api/v1/projects/{id}/upload-url` | URL assinada (POST multipart) para enviar o MP4 direto ao storage |
| POST | `/api/v1/projects/{id}/finalize-upload` | Valida o arquivo enviado com FFprobe (idempotente) |
| GET | `/api/v1/projects/{id}/transcript` | Segmentos transcritos com timestamps |
| GET | `/api/v1/projects/{id}/candidates` | Trechos avaliados pela IA (`?only_selected=true`) |
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

   Se válido, o projeto passa para `uploaded`. Se inválido, o arquivo é **apagado**, o projeto continua em
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
| `render` | Etapa 6 — hoje falha com `stage_unavailable` |

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

Erros do worker: `no_candidates`, `analysis_refused`, `analysis_failed`, `analysis_unavailable`,
`analysis_misconfigured`, `no_speech`, `audio_extraction_failed`, `transcription_failed`, `transcription_unavailable`,
`transcription_misconfigured`, `stage_unavailable`, `source_missing`, `source_changed`, `storage_error`, `insufficient_disk`,
`stage_timeout`, `timeout`, `resource_limit`, `worker_lost`, `internal_error` (mensagem genérica ao usuário;
detalhe técnico, sem segredos, em `processing_jobs.error_detail`).

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

### Worker no Windows

O RQ depende de `fork()`, que não existe no Windows. Opções:

1. **Recomendado:** worker em contêiner: `docker compose --profile worker up -d --build worker`
   (usa `backend/.env` e aplica limites de CPU/memória do Docker).
2. **WSL2 (Ubuntu):** siga as instruções de Linux dentro do WSL e rode `python -m app.worker.run`.
3. Nativo: `python -m app.worker.run` usa um worker sem fork, com timeout por temporizador e **sem** limites
   de memória/CPU. Use só para testes locais — esse modo não foi testado em Windows.

### Testes

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
