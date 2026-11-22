# Implantação do beta fechado

Uma VM com Docker Compose, HTTPS automático (Caddy) e armazenamento de objetos gerenciado (S3 ou R2).
Arquivos: [`deploy/`](../deploy/) e `frontend/Dockerfile`, `backend/Dockerfile`.

> **Estado (22/11/2026)**: imagens construídas e **pilha de produção completa executada localmente** com este
> mesmo Compose e Caddyfile (domínios `*.localhost` com certificado interno do Caddy, S3 emulado no lugar do
> R2/S3, sem chave do Claude). Verificado: 10 serviços no ar, `migrate` (migrações + bucket privado com CORS),
> `/health/ready` 200 (503 sem worker), `/docs` 404, HTTPS com HSTS e cabeçalhos de segurança, convidado ×
> não convidado (403), upload direto + validação no contêiner, worker transcrevendo com Whisper `small` (6 GB de
> RAM e 8 GB virtuais aplicados), parada honesta sem chave do Claude, comando de administração, backup e alerta
> "nenhum worker vivo". A **suíte completa do backend (246 testes) passa dentro da imagem de produção**
> (Debian 13: FFmpeg 7.1, Python 3.12). Falta na VM: certificado real (Let's Encrypt), provedores reais.

## 1. Decisões e contas necessárias

| Item | Recomendação |
|---|---|
| VM | 4 vCPU, 16 GB de RAM, 60 GB SSD, Ubuntu 24.04 LTS ([CAPACIDADE.md](CAPACIDADE.md)) |
| Armazenamento | Bucket privado em S3 ou R2 (o R2 não cobra saída de dados) |
| Domínio | Dois nomes: `APP_DOMAIN` (frontend) e `API_DOMAIN` (API) apontando para o IP da VM |
| Supabase | Projeto com cadastro público desligado |
| Anthropic | Chave de API |
| Alertas | Webhook do Slack ou Discord |

## 2. Preparar a VM

```bash
# usuário administrativo com chave SSH (no seu computador: ssh-copy-id usuario@IP)
sudo adduser autoclip && sudo usermod -aG sudo autoclip
# SSH só por chave, sem root
sudo sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/; s/^#\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
# firewall: SSH, HTTP e HTTPS (restrinja a porta 22 ao seu IP, se possível)
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
# atualizações automáticas de segurança
sudo apt-get install -y unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades
```

Instale o Docker Engine e o plugin Compose pelo repositório oficial (docs.docker.com/engine/install/ubuntu).
Quem está no grupo `docker` tem poder de administrador na VM: só o operador do beta.

**Ative os backups/snapshots do provedor da VM**: o backup do Postgres (seção 7) fica no disco da própria VM.

## 3. DNS

Crie registros `A` (e `AAAA`, se houver IPv6) para `APP_DOMAIN` e `API_DOMAIN` com o IP da VM. O Caddy obtém
os certificados sozinho quando os nomes já resolvem para a VM.

## 4. Armazenamento

1. Crie um bucket **privado**.
2. Crie uma credencial com acesso **somente** a esse bucket.
3. Preencha `S3_*` e `STORAGE_PUBLIC_ORIGIN` no `.env`. O serviço `migrate` executa `python -m app.storage_setup`
   em todo `up`: aplica CORS para `S3_CORS_ORIGINS` e o bloqueio de acesso público (onde o provedor suporta).
4. Recomendado: regra de ciclo de vida para abortar uploads multipartes incompletos após 1 dia.

## 5. Supabase

- Authentication > Providers > Email: **desligar** "Allow new users to sign up".
- Authentication > URL Configuration: Site URL `https://APP_DOMAIN`; Redirect URLs
  `https://APP_DOMAIN/entrar` e `https://APP_DOMAIN/redefinir-senha`.
- Authentication > Email Templates: traduza "Invite user" e "Reset password" para português.
- A API valida os tokens pelas chaves públicas do projeto (`SUPABASE_URL`); a chave `service_role` fica só no
  `.env` do servidor (exclusão de conta e envio de convites).

## 6. Subir a aplicação

```bash
git clone <repositório> autoclip && cd autoclip
cp deploy/.env.example deploy/.env && chmod 600 deploy/.env && nano deploy/.env   # preencha tudo que tem <>
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build
# baixa o modelo Whisper uma vez (fica no volume whispermodels)
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env run --rm worker python -m app.transcription.download
```

Para facilitar: `alias ac='docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env'`.

Serviços: `caddy` (HTTPS), `frontend`, `api` (2 processos), `migrate` (migrações + storage, roda e termina),
`worker` (1 worker, 6 GB de RAM), `maintenance` (recuperação, retenção, alertas e relatório diário), `postgres`,
`redis` (com persistência), `backup` (dump diário). `worker2` só com `--profile worker2`.

## 7. Backup e restauração

O serviço `backup` gera um dump diário em `/backups` (volume `backups`) e apaga os com mais de
`BACKUP_KEEP_DAYS` dias. **Antes de convidar alguém, teste a restauração**:

```bash
ac exec backup sh /backup.sh /backups                       # backup agora
ac exec backup ls -l /backups
ac exec backup psql -d postgres -c "CREATE DATABASE restore_teste"
ac exec backup sh -c 'pg_restore --no-owner --dbname=restore_teste $(ls -t /backups/*.dump | head -1)'
ac exec backup psql -d restore_teste -c "select count(*) from projects"   # compare com o banco autoclip
ac exec backup psql -d postgres -c "DROP DATABASE restore_teste"
```

Cópia para fora da VM (recomendado): copie periodicamente o volume `backups` para outro local (por exemplo um
bucket separado com `rclone`). `deploy/restore.sh` documenta a restauração completa.

## 8. Operação do beta

```bash
ac exec api python -m app.admin convidar pessoa@dominio.com --enviar-email
ac exec api python -m app.admin periodo --todos --inicio 2026-12-01 --fim 2026-12-14
ac exec api python -m app.admin relatorio
ac exec api python -m app.admin usuarios
ac logs -f --tail=100 api worker maintenance
docker stats                                               # memória e CPU por serviço
```

**Segundo worker**: ative com `ac --profile worker2 up -d` quando o relatório mostrar espera na fila acima de
15 min com frequência **e** `docker stats` mostrar folga de memória (cada worker pode usar até ~4,3 GB).

**Atualizar**: `git pull`, aumente `APP_VERSION`, faça um backup (seção 7) e rode o `up -d --build` de novo
(o `migrate` aplica as migrações). Para voltar atrás, faça checkout da versão anterior e rode o `up` de novo;
se houve migração, restaure o backup.

## 9. Verificação antes de convidar (lista mínima)

1. `curl -s https://API_DOMAIN/health/ready` → `"pronto": true` (banco, Redis, armazenamento, worker).
2. `curl -sI https://APP_DOMAIN` → HTTPS válido, `strict-transport-security`, `x-frame-options: DENY`,
   `content-security-policy-report-only`.
3. `curl -s https://API_DOMAIN/docs` → 404 (documentação desligada em produção).
4. Convidar o próprio e-mail, aceitar o convite, criar senha, entrar.
5. Enviar um vídeo curto com fala real: processar até "Concluído", assistir, baixar, regenerar.
6. Enviar um vídeo de ~60 min: acompanhar `docker stats` (memória do worker abaixo de 6 GB) e o tempo total.
7. Um e-mail não convidado recebe "beta fechado".
8. Alerta de teste: pare o worker (`ac stop worker`) e confira o alerta "Nenhum worker vivo" no canal em até
   5 min; depois `ac start worker`.
9. Teste de restauração (seção 7).
10. Excluir uma conta de teste e confirmar que o login some no Supabase.

## 10. O que não foi testado neste ambiente

- Certificado real do Let's Encrypt (localmente o Caddy usou o certificado interno para `*.localhost`).
- S3/R2, Supabase e Claude reais (Lote 7, `python -m app.validar`).
- Desempenho na VM escolhida (o teste de capacidade foi feito num contêiner de 4 vCPU / 16 GB).
