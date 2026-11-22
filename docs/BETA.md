# Beta fechado — liberação e acompanhamento

10 convidados · 14 dias · vídeos de até 60 min. Comandos abaixo pressupõem o alias da
[IMPLANTACAO.md](IMPLANTACAO.md): `alias ac='docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env'`.

## 1. Validação das integrações reais (Lote 7)

Na VM, com o `deploy/.env` preenchido:

```bash
ac exec api python -m app.validar armazenamento --mb 200
# crie um usuário de teste convidado (ex.: o seu e-mail), defina a senha pelo link do convite e então:
ac exec -e VALIDACAO_EMAIL=... -e VALIDACAO_SENHA=... api python -m app.validar supabase
ac exec api python -m app.validar claude --lotes 2     # custo real aproximado: US$ 0,10–0,40
```

| Resultado esperado | Se reprovar |
|---|---|
| Armazenamento: as 10 verificações **APROVADAS** (bucket privado, URL expira, limite de tamanho e de tipo no upload aplicados pelo provedor, CORS só para o frontend, cópia estimada abaixo de 240 s para 1,13 GB) | Corrija a configuração do bucket/credencial; não abra o beta com upload sem limite aplicado pelo provedor ou CORS aberto |
| Supabase: JWKS, token real aceito, chave de serviço válida | Revise `SUPABASE_URL` e as chaves |
| Claude: resposta estruturada válida, gancho > fala vazia, instrução embutida ignorada, custo medido | Veja o código de erro; ajuste `LLM_RESERVE_USD_PER_MINUTE` para o valor sugerido |

Os testes automatizados (Pytest e Playwright) não vão para as imagens: eles rodam no repositório antes de cada
versão, e a VM só recebe versões com as suítes verdes e `scripts/check_protected_paths.sh` aprovado.

## 2. Checklist de liberação (tudo marcado antes do primeiro convite)

**Infraestrutura**
- [ ] `ac ps`: todos os serviços `running`/`healthy`; `migrate` terminou com sucesso
- [ ] `https://API_DOMAIN/health/ready` → `"pronto": true`
- [ ] HTTPS válido nos dois domínios; `/docs` responde 404
- [ ] Backup gerado e **restauração testada** (IMPLANTACAO.md, seção 7); snapshots do provedor ativos
- [ ] Modelo Whisper baixado no volume (`python -m app.transcription.download`)

**Integrações**
- [ ] `app.validar armazenamento`, `supabase` e `claude` sem reprovações
- [ ] `LLM_RESERVE_USD_PER_MINUTE` ajustado ao custo medido; tetos `LLM_BUDGET_*` revisados
- [ ] Alerta de teste recebido no canal (pare o worker por 5 min)

**Produto**
- [ ] Vídeo real (fala humana) processado de ponta a ponta: cortes assistidos, baixados, regenerados
- [ ] Vídeo de ~60 min processado com `docker stats` mostrando o worker abaixo de 6 GB
- [ ] E-mail não convidado recebe "beta fechado"; cadastro aberto desativado no Supabase
- [ ] Exclusão de conta de teste remove o login no Supabase

**Privacidade e comunicação**
- [ ] `SUPPORT_EMAIL`, `STORAGE_PROVIDER_NAME` e `HOSTING_PROVIDER_NAME` preenchidos (página `/privacidade`)
- [ ] Texto da política revisado (ou aviso de beta mantido) e termos do beta enviados aos convidados (seção 6)
- [ ] Período do beta aplicado: `ac exec api python -m app.admin periodo --todos --inicio AAAA-MM-DD --fim AAAA-MM-DD`
- [ ] Data de encerramento e destino dos dados decididos (seção 7)

## 3. Acompanhamento

**Diário** (5 min): relatório automático no canal de alertas, ou `ac exec api python -m app.admin relatorio`.
- falhas por código (alguma nova? `resource_limit` ou `worker_lost` repetidos indicam memória/infra);
- espera na fila (acima de 15 min com frequência → considerar o segundo worker);
- custo de IA no período e reservado (comparar com o teto);
- avaliações dos cortes (útil × não útil).

**Semanal**:
- `ac exec api python -m app.admin usuarios` — quem está usando, quem não começou (enviar lembrete);
- média de nota estimada dos cortes úteis × não úteis (a seleção está acertando?);
- `ac logs maintenance | grep -i reten` — retenção rodando sem falhas;
- espaço em disco (`df -h`) e volume de backups.

## 4. Critérios para pausar o beta

Pausar = `BETA_MODE=true` com `BETA_ALLOWED_EMAILS` vazio e revogar convites (`app.admin revogar`), ou parar o
`worker` para interromper o processamento (a API continua mostrando os cortes já gerados).

| Situação | Ação |
|---|---|
| Custo de IA atingiu o teto total | Automático: novos processamentos recusados (`beta_budget_exhausted`); decidir se amplia |
| Suspeita de acesso indevido a dados de outro usuário | **Pausar imediatamente**; investigar com a auditoria; ver seção 5 |
| Mais de 30% dos processamentos falhando em 24 h | Parar o worker, investigar falhas por código |
| Perda de dados ou backup falhando 2 dias seguidos | Pausar novos envios até restaurar a rotina de backup |
| Vídeo com conteúdo ilegal ou de terceiros sem autorização | Suspender o usuário (`app.admin suspender`), excluir o projeto, registrar |

## 5. Resposta a incidentes

1. **Conter**: suspender usuário, revogar convites ou parar o worker conforme o caso.
2. **Registrar**: horário, o que aconteceu, quem foi afetado (a auditoria tem `project.*`, `clip.*`, `admin.*`,
   `account.*`; o relatório e os logs têm falhas e alertas).
3. **Preservar evidências**: copiar logs (`ac logs --since 24h > incidente.log`) antes de reiniciar serviços.
4. **Corrigir e testar** no repositório (suíte completa) antes de publicar a correção.
5. **Comunicar** os afetados. Incidente de segurança com dados pessoais: avaliar comunicação à ANPD e aos
   titulares (LGPD art. 48) com orientação jurídica.

## 6. Mensagem aos convidados (modelo — revisar antes de enviar)

> Olá! Você foi convidado(a) para o beta fechado do AutoClip IA, de [DATA INÍCIO] a [DATA FIM].
>
> - Você receberá um e-mail para criar sua senha. Durante o beta, cada pessoa pode processar até [180] minutos
>   de vídeo, [1] vídeo por vez, com até 60 minutos por vídeo.
> - Envie apenas vídeos que você tem direito de usar (seus ou com autorização de quem aparece e de quem detém
>   os direitos).
> - Como tratamos seus dados: o vídeo fica em armazenamento privado; a fala é transcrita em nossos servidores
>   e o texto é analisado por IA (Claude, da Anthropic) para escolher os melhores momentos. O vídeo original é
>   apagado [7] dias após o processamento e o projeto inteiro [30] dias depois. Detalhes em [APP]/privacidade.
> - Você pode excluir projetos ou sua conta a qualquer momento na página "Minha conta".
> - É um beta: pode haver falhas. Seu retorno ajuda muito — use os botões "Útil / Não útil" em cada corte e
>   fale com a gente em [SUPPORT_EMAIL].

## 7. Encerramento do beta

1. Avisar os convidados com [N] dias de antecedência (data de encerramento e o que acontece com os dados).
2. No dia: revogar convites (`app.admin revogar`) ou manter, conforme a decisão de migração.
3. Dados — **decisão pendente** (escolha antes de começar e informe na mensagem aos convidados):
   - **Apagar**: reduzir `RETENTION_PROJECT_DAYS` ou excluir as contas; a retenção apaga tudo; confirmar com
     `app.admin usuarios` e com o bucket vazio em `users/`.
   - **Migrar**: manter contas e projetos para a próxima versão, com aviso e consentimento.
4. Relatório final: `app.admin relatorio --horas 336 --json` (14 dias), custo real total, falhas, avaliações dos
   cortes, principais aprendizados e pendências.
