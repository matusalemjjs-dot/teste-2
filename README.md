# AutoClip IA

Plataforma SaaS que transforma vídeos longos em cortes curtos usando transcrição, análise semântica por IA e
processamento com FFmpeg. Desenvolvida de forma incremental — veja [docs/ROADMAP.md](docs/ROADMAP.md) e
[docs/CHECKLIST.md](docs/CHECKLIST.md).

> **Estado atual: Etapa 1 (fundação do backend).** Ainda **não** há upload, fila, transcrição, IA,
> renderização nem frontend. Nada neste repositório simula resultados de processamento.

## Estrutura

```
backend/            API FastAPI (Python 3.12+)
  app/core/         configuração, logs estruturados, validação de JWT
  app/models/       modelo de dados SQLAlchemy (10 tabelas)
  app/api/v1/       rotas
  app/services/     regras de negócio (máquina de estados de projetos, auditoria)
  alembic/          migrações
  tests/            Pytest (usa PostgreSQL real)
infra/              scripts de infraestrutura local
docker-compose.yml  PostgreSQL 16 e Redis 7
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
| POST | `/api/v1/projects/{id}/cancel` | Cancelar (idempotente) |
| DELETE | `/api/v1/projects/{id}` | Excluir (bloqueado durante processamento) |

Todas as rotas `/api/v1/*` exigem `Authorization: Bearer <access_token do Supabase>`. Projetos de outros
usuários retornam 404 (indistinguíveis de inexistentes).

## Executar no Windows

Pré-requisitos: [Python 3.12+](https://www.python.org/downloads/) (marque "Add to PATH"),
[Docker Desktop](https://www.docker.com/products/docker-desktop/) e Git.

No PowerShell, na pasta do repositório:

```powershell
# 1. Infraestrutura (PostgreSQL + Redis)
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

### Testes

Os testes usam o banco `autoclip_test` (criado automaticamente pelo `docker compose` na primeira subida):

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

## Segurança e privacidade

- Segredos apenas em variáveis de ambiente (`.env` está no `.gitignore`).
- Logs em JSON com remoção de tokens, senhas e chaves.
- Toda consulta de recurso filtra por `owner_id`.
- A criação de projeto exige `rights_confirmed: true` (o usuário declara ter autorização sobre o conteúdo).
- Excluir um perfil remove em cascata projetos, mídias, transcrições e cortes; registros de uso e auditoria são
  mantidos com o vínculo anulado.
