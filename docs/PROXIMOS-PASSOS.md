# O que falta para abrir o beta — instruções para o responsável

O código, os testes e a pilha de produção estão prontos e verificados localmente
([CHECKLIST.md](CHECKLIST.md)). O que falta depende de **contas, decisões e chaves** que só você pode
providenciar. Siga na ordem; cada passo diz o que fazer, onde e o que guardar.

> **Regra dos segredos**: chaves e senhas vão **somente** no arquivo `deploy/.env` dentro da VM
> (`chmod 600`). Nunca no chat, no repositório, em e-mail ou em print de tela.

## Resumo

| # | Item | Tipo | Tempo estimado |
|---|---|---|---|
| 1 | Contratar a VM | Conta | 30 min |
| 2 | Domínio e DNS | Conta | 15 min (+ propagação) |
| 3 | Armazenamento (R2 ou S3) | Conta + chave | 20 min |
| 4 | Supabase (login e convites) | Conta + chaves | 20 min |
| 5 | Anthropic (Claude) | Conta + chave | 10 min |
| 6 | Canal de alertas (Slack ou Discord) | Conta | 10 min |
| 7 | Decisões do beta | Decisão | — |
| 8 | Implantar e validar | Execução | 2–3 h |
| 9 | Liberar os convites | Execução | 30 min |
| 10 | (Opcional) validar as instruções no Windows | Teste | 1 h |

## 1. VM

- **Configuração mínima**: 4 vCPU, 16 GB de RAM, 60 GB de SSD, Ubuntu 24.04 LTS, IP fixo
  (medido em [CAPACIDADE.md](CAPACIDADE.md): um vídeo de 60 min usa até ~4,3 GB de RAM no worker).
- Qualquer provedor serve (ex.: Hetzner, DigitalOcean, AWS Lightsail/EC2, Google Cloud, Azure, Oracle).
- Ative **backups/snapshots automáticos** do provedor.
- Acesso por **chave SSH** (não por senha).
- **Guarde**: IP da VM e nome do provedor (vai em `HOSTING_PROVIDER_NAME`, mostrado na página de privacidade).

## 2. Domínio e DNS

- Escolha dois nomes, por exemplo `beta.seudominio.com.br` (site) e `api.beta.seudominio.com.br` (API).
- No painel do domínio, crie registros **A** dos dois nomes apontando para o IP da VM.
- Confira: `ping beta.seudominio.com.br` deve responder com o IP da VM (pode levar até algumas horas).
- **Guarde**: os dois nomes → `APP_DOMAIN`, `API_DOMAIN`. Um e-mail seu para o certificado → `ACME_EMAIL`.

## 3. Armazenamento de vídeos

**Recomendado: Cloudflare R2** (não cobra saída de dados, que é o download dos cortes).

1. Crie um bucket **privado** (ex.: `autoclip-beta`). Não ative acesso público nem domínio público.
2. Crie um token de API com permissão de **leitura e escrita somente nesse bucket**.
3. Recomendado: regra de ciclo de vida "abortar uploads multipartes incompletos após 1 dia".
4. **Guarde** (vão no `deploy/.env`):
   - `S3_BUCKET` = nome do bucket
   - `S3_REGION` = `auto` (R2) ou a região (S3, ex.: `sa-east-1`)
   - `S3_ENDPOINT_URL` e `S3_PUBLIC_ENDPOINT_URL` = `https://<ID-DA-CONTA>.r2.cloudflarestorage.com`
     (no AWS S3, deixe vazios)
   - `STORAGE_PUBLIC_ORIGIN` = a origem do endpoint público (no R2, o mesmo endereço acima; no S3, a origem que
     aparece na URL de envio — a validação do passo 8 confirma se o navegador consegue enviar)
   - `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` = credenciais do token (**segredo**)
   - `STORAGE_PROVIDER_NAME` = ex.: `Cloudflare R2`

O CORS e o bloqueio de acesso público são aplicados automaticamente pela aplicação na subida.

## 4. Supabase (login, convites e exclusão de conta)

1. Crie um projeto em supabase.com (região: São Paulo, se disponível).
2. **Authentication > Providers > Email**: desligue **"Allow new users to sign up"** (beta só por convite).
3. **Authentication > URL Configuration**: Site URL `https://APP_DOMAIN`; Redirect URLs
   `https://APP_DOMAIN/entrar` e `https://APP_DOMAIN/redefinir-senha`.
4. **Authentication > Email Templates**: traduza "Invite user" e "Reset password" para português.
5. **Project Settings > API** — **guarde**:
   - `SUPABASE_URL` = Project URL
   - `SUPABASE_ANON_KEY` = chave `anon`/publishable (pública, vai para o site)
   - `SUPABASE_SERVICE_ROLE_KEY` = chave `service_role`/secret (**segredo**; só no servidor, nunca no site)

## 5. Anthropic (Claude — escolha dos melhores momentos)

1. Crie a conta em console.anthropic.com e adicione créditos.
2. **Defina um limite de gasto mensal** no console (ex.: US$ 50) — além dos tetos da aplicação
   (US$ 3 por usuário e US$ 40 no total do beta).
3. Crie uma chave de API → `ANTHROPIC_API_KEY` (**segredo**).

## 6. Canal de alertas

- **Slack**: crie um app com "Incoming Webhooks" num canal privado. **Discord**: Configurações do canal >
  Integrações > Webhooks.
- **Guarde**: a URL → `ALERT_WEBHOOK_URL` (**segredo**: quem tem a URL pode postar no canal) e
  `ALERT_WEBHOOK_FORMAT` = `slack` ou `discord`.

O canal recebe: worker parado, fila atrasada, falhas repetidas, teto de custo e o relatório diário.

## 7. Decisões do beta

| Decisão | Opções | Onde entra |
|---|---|---|
| Datas do beta (14 dias) | início e fim | `app.admin periodo`, mensagem aos convidados |
| E-mail de suporte | o seu e-mail ou um dedicado ao beta | `SUPPORT_EMAIL` |
| Lista de convidados (até 10) | e-mails | `app.admin convidar` |
| Dados ao fim do beta | **apagar tudo** ou **migrar** com consentimento | [BETA.md](BETA.md), seção 7, e mensagem aos convidados |
| Texto de privacidade | revisão jurídica ou manter o aviso de beta | página `/privacidade` |

## 8. Implantar e validar

Com os itens 1–7 prontos, na VM:

1. Preparar a VM, DNS e subir a aplicação: [IMPLANTACAO.md](IMPLANTACAO.md), seções 2 a 6.
2. Preencher `deploy/.env` a partir de `deploy/.env.example` com os valores guardados acima.
3. Validar as integrações reais ([BETA.md](BETA.md), seção 1):
   `app.validar armazenamento`, `supabase` e `claude` — todos sem reprovações.
4. Ajustar `LLM_RESERVE_USD_PER_MINUTE` para o valor sugerido pela validação do Claude.
5. Verificação antes de convidar: [IMPLANTACAO.md](IMPLANTACAO.md), seção 9 (inclui um vídeo real de ~60 min
   e o teste de restauração do backup).

**Como posso ajudar nesta etapa**: você executa os comandos dos documentos e me envia as **saídas** (sem
segredos) para eu analisar; ou conecte o seu computador a uma sessão (Remote Control no app do Claude) e eu
executo pela sua conexão SSH, com sua aprovação a cada etapa. Os segredos você digita direto no `deploy/.env`.

## 9. Liberar os convites

Siga o checklist de liberação de [BETA.md](BETA.md), seção 2 — **tudo marcado antes do primeiro convite**.
Depois: `app.admin periodo`, `app.admin convidar <email> --enviar-email` e a mensagem aos convidados
(BETA.md, seção 6). Acompanhamento diário e semanal: BETA.md, seção 3.

## 10. (Opcional) Instruções para Windows

As instruções do [README](../README.md#executar-no-windows) nunca foram executadas num Windows. Para validar:
num PC com Windows 10/11, siga o README do início (Docker Desktop, Python, Node, FFmpeg) e anote qualquer passo
que falhar. Isso afeta só o ambiente de desenvolvimento, não o beta.

## O que **não** precisa de você

Código, testes (246 do backend, 30 de navegador), imagens Docker, pilha de produção, backup, alertas,
retenção, cotas e documentação já estão prontos e verificados.
