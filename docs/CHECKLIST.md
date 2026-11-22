# Checklist de validação

Legenda: [x] implementado e coberto por teste automatizado executado · [~] parcial · [ ] pendente

## Etapa 1
- [x] `GET /health` responde 200 com banco disponível (503 se indisponível — sem teste automatizado)
- [x] Todas as 10 tabelas criadas pela migração `0001`; downgrade/upgrade funcionam
- [x] Modelos e migração sincronizados (autogenerate não detecta diferenças)
- [x] Tokens Supabase validados: assinatura, expiração, audience, issuer, role; `alg=none` rejeitado
- [x] Suporte a HS256 (segredo legado) e ES256 via JWKS (teste com JWKS simulado)
- [x] Perfil criado no primeiro acesso, de forma idempotente
- [x] Usuário não lê, cancela nem exclui projeto de outro usuário (404)
- [x] Criação exige confirmação de autorização sobre o conteúdo
- [x] Erros de validação em português
- [x] Cancelamento idempotente; exclusão bloqueada durante processamento
- [x] Exclusão de perfil remove projetos em cascata; auditoria preservada com ator anulado
- [x] Logs JSON com remoção de tokens/segredos
- [ ] Validação com um projeto Supabase real (depende de configuração do usuário)

## Etapa 2
- [x] URL assinada (POST) emitida só para o dono do projeto; chave gerada pelo servidor
- [x] Política assinada com limite de tamanho e Content-Type (conteúdo da política verificado em teste)
- [ ] Storage recusa arquivo maior que o limite no envio — **não testado**: o emulador moto não aplica a política;
      validar com MinIO/S3 real. O tamanho real é conferido de novo no finalize (testado)
- [x] Pedido de URL repetido reutiliza o mesmo asset
- [x] MP4 válido (moov no fim, faststart, vertical, áudio mais curto) é aceito; duração/dimensões gravadas
- [x] Rejeitados com mensagem: texto renomeado, MKV e MOV renomeados, sem áudio, curto, longo, truncado (2 tipos)
- [x] Arquivo inválido é apagado do storage; projeto volta a aceitar upload
- [x] Finalize idempotente; finalize concorrente retorna 409
- [x] FFprobe ausente → 503 sem invalidar o upload
- [x] Excluir projeto apaga os arquivos; storage indisponível → 503 e nada é apagado
- [x] Metadados livres (título, GPS) não são armazenados
- [x] Assinaturas de URL removidas dos logs
- [ ] `docker compose up` com MinIO — **não executado** (sem Docker daemon no ambiente de desenvolvimento)
- [x] Envio pelo navegador (CORS) — verificado no E2E da Etapa 8 (S3 emulado)

## Etapa 3
- [x] `POST /process` exige upload validado; isolamento por usuário (404)
- [x] Requisição repetida devolve o mesmo job; um único job no banco e na fila
- [x] Reexecutar a tarefa de um job já tratado não processa de novo
- [x] Pipeline padrão termina `failed/stage_unavailable` — nunca `completed` sem processamento real
- [x] Etapa `prepare` baixa o vídeo, confere tamanho e grava SHA-256; arquivo alterado → `source_changed`
- [x] Erro inesperado vira `failed/internal_error` com mensagem genérica; detalhe interno sem tokens
- [x] Falha de um job não impede o próximo da fila
- [x] Nova tentativa após falha; limite de tentativas; falhas de infraestrutura não contam
- [x] Fila indisponível → 503 e projeto `failed/queue_unavailable`; nova tentativa funciona depois
- [x] Cancelar na fila remove o job do RQ; cancelar durante o processamento interrompe etapa e subprocesso
- [x] Timeout por subprocesso e timeout total do job (RQ)
- [x] Temporários removidos em sucesso, falha, cancelamento e timeout; órfãos antigos limpos
- [x] Job perdido (worker morto) é detectado; processo do job morto marca falha
- [x] Limites de memória/CPU/arquivo aplicados no processo do job (verificado com worker real em processo separado)
- [ ] Estouro real do limite de memória durante FFmpeg — não reproduzido em teste (o vídeo de teste é pequeno)
- [ ] Worker nativo no Windows e worker em Docker — **não executados**

## Etapa 4
- [x] Áudio extraído com FFmpeg (WAV 16 kHz mono para o Whisper local, MP3 32 kbps para o serviço externo)
- [x] Transcrição real com faster-whisper (`tiny`) de fala em português: timestamps em ordem, sem sobreposição,
      dentro da duração; palavras-chave reconhecidas; silêncio final sem segmentos
- [x] Segmentos e timestamps por palavra gravados em `transcript_segments`; `GET /transcript` com isolamento
- [x] Reprocessar não duplica segmentos
- [x] Vídeo sem fala → `no_speech`
- [x] Provedor trocável por configuração; provedor inválido/sem chave → `transcription_misconfigured`
- [x] Adaptador externo: requisição e resposta testadas contra servidor HTTP local (200, 400, 429, 500)
- [ ] Adaptador externo contra a API real da OpenAI — **não executado** (requer chave)
- [x] Cancelamento durante a transcrição não grava resultado
- [x] Provedor indisponível não consome tentativas
- [x] Worker real (fork) transcreve com limite de memória de 4 GB aplicado
- [x] Desempenho medido (60 s de fala, CPU, 2 threads): `tiny` 3,1 s; `small` 9,2 s (+9 s de carga do modelo)
- [ ] Vídeo real de 60 min — **não executado**; estimativa por extrapolação: ~9 min com `small`

## Etapa 5
- [x] Janelas de 15–60 s alinhadas a segmentos, preferindo fim de frase; sem quase-duplicatas; limite e cobertura
- [x] Fala insuficiente → `no_candidates` sem chamar a IA
- [x] Validação descarta id inexistente/duplicado, notas fora de 0–10, campos ausentes; trunca títulos longos
- [x] Nota 0–100 ponderada; qualidade técnica calculada pela transcrição
- [x] Seleção até 10, sem sobreposição, dentro da duração; todos os avaliados gravados com `selected`
- [x] Reprocessar substitui os candidatos; consumo de tokens registrado uma vez por job
- [x] Adaptador Claude: requisição (modelo, JSON Schema, effort, fallbacks, beta) e respostas testadas contra
      servidor HTTP local (sucesso, recusa, truncamento, JSON inválido, 400, 401, 429, 529)
- [x] Sem chave → `analysis_misconfigured` (não consome tentativa); recusa → `analysis_refused`
- [x] Cancelar durante a análise não grava candidatos; isolamento em `/candidates`
- [ ] **Avaliação real com o Claude — não executada** (sem `ANTHROPIC_API_KEY` neste ambiente). Rodar
      `pytest tests/test_llm_real.py` com a chave definida
- [ ] Compatibilidade de `fallbacks` + `output_config.format` na API real — **não verificada**
- [ ] Custo real por hora de vídeo — **não medido**
- [ ] Uma falha intermitente não identificada em ~12 execuções da suíte (não reproduzida depois; suspeita: testes
      sensíveis a tempo, como cancelamento por temporizador)

## Etapa 6
- [x] Saída 1080x1920, H.264/AAC, `yuv420p`, pixels quadrados, duração correta — fontes paisagem, retrato,
      baixa resolução e com rotação, nos dois layouts
- [x] Legenda queimada visível na região esperada do quadro (comparação de pixels com e sem legenda)
- [x] Legendas a partir de timestamps por palavra; sem sobreposição; fallback sem timestamps; SRT válido
- [x] Pipeline completo termina `completed` (progresso 100) com um corte por trecho selecionado
- [x] Miniatura JPEG acessível por URL temporária
- [x] Download do MP4 por URL assinada (expira em 300 s, `attachment`); modo `inline` para reprodução; SRT
- [x] Isolamento: outro usuário recebe 404 em listar, consultar, baixar e regenerar
- [x] Regenerar: idempotente, nova versão com outro layout, versão antiga removida do storage
- [x] Falha na regeneração mantém a versão anterior baixável
- [x] Falha parcial e total na renderização; reprocessar após falha não duplica cortes
- [x] Projeto com regeneração pendente não pode ser excluído; excluir remove todos os arquivos
- [x] Consumo de renderização registrado por corte e versão
- [ ] Fluxo completo com o Claude real — depende de `ANTHROPIC_API_KEY` (os testes usam avaliador de teste)
- [ ] Tempo de renderização com o preset padrão (`veryfast`) para cortes de 60 s — não medido sistematicamente
      (amostra: 20 s de vídeo 720p → 3,6 s em `center_crop`, 8,3 s em `blur_background`)

## Etapa 7
- [x] Minutos debitados só no sucesso; uma vez por projeto (repetição e reexecução não cobram de novo)
- [x] Reserva de projetos em fila/processando conta na cota; excedeu → 402 sem enfileirar nada
- [x] Trava da assinatura serializa pedidos simultâneos (teste determinístico; falha se a trava for removida)
- [x] Cota de um usuário não afeta outro; novo mês libera a cota
- [x] Projetos por dia não podem ser burlados excluindo projetos; janela de 24 h
- [x] Conta suspensa → 403; cotas desativáveis por configuração
- [x] Custo interno estimado registrado
- [x] Rate limit geral e para operações caras, por usuário, com `Retry-After`; janela reinicia
- [x] Redis indisponível não derruba a API (fail-open); `/health` fora do limite
- [ ] Limite por IP para requisições não autenticadas — não implementado (tokens JWT, sem senha na API)
- [ ] Valores de custo por minuto reais — dependem da sua infraestrutura (padrão 0)

## Etapa 8 (Playwright, pilha real)
- [x] Rota protegida redireciona para `/entrar`; token inválido não acessa dados; sair limpa a sessão
- [x] Fluxo completo no navegador: criar projeto → upload direto ao storage (CORS) com progresso → validação →
      processamento real (transcrição, renderização) → galeria → reprodução 1080x1920 → download MP4 → regeneração
- [x] Upload inválido (não é vídeo; extensão errada) recusado com mensagem clara
- [x] Confirmação de direitos obrigatória; usuário não vê projeto de outro; cancelar e excluir
- [x] Painel mostra vídeos processados e minutos consumidos
- [x] Acessibilidade: nenhuma violação séria/crítica (axe, WCAG 2 A/AA) nas telas públicas, painel e novo projeto
- [x] Responsivo: sem rolagem horizontal no celular (Pixel 7)
- [x] `npm run lint`, `npm run typecheck` e `next build` sem erros
- [ ] Cadastro, login, logout e recuperação de senha com um projeto Supabase real — **não testados** (sem projeto
      Supabase neste ambiente); o código usa `signUp`, `signInWithPassword`, `resetPasswordForEmail`,
      `updateUser` e `onAuthStateChange` do supabase-js
- [ ] Acessibilidade da galeria com leitor de tela real — não testada (só verificação automática)
- [ ] Frontend no Windows — não executado

## Etapa 9 — Lote 1 (robustez para o beta)
- [x] F1: validação feita sobre cópia imutável; troca do arquivo **depois** e **durante** a validação não chega
      ao processamento (hash do vídeo processado = hash do enviado); validações simultâneas geram uma única cópia;
      falha na cópia → 503 sem estado inválido; upload inválido remove original e cópia
- [x] F2: Redis zerado → job devolvido à fila e processado uma única vez (uma cobrança); 3 manutenções
      simultâneas → uma única devolução; job vivo na fila não é tocado; carência para jobs recém-criados;
      limite de devoluções → `queue_lost` sem consumir tentativa; Redis fora do ar não quebra a manutenção
- [x] F2b: job longo com sinal de vida não é marcado como perdido; job sem sinal de vida é detectado;
      **worker real morto com SIGKILL** no meio de um job → detectado, reprocessado e cobrado uma única vez
- [x] F3: regeneração presa na fila não bloqueia a exclusão (arquivos e linhas removidos; o worker ignora o job);
      job rodando → 409 até terminar; projeto travado → 409; corrida exclusão × início do worker (4 rodadas)
      sem exceção, job órfão ou arquivo esquecido
- [x] Testes de recuperação estáveis em 4 execuções seguidas
- [x] `scripts/check_protected_paths.sh` sem alterações nos caminhos protegidos (e falha quando há alteração)
- [x] Mudança de comportamento aprovada: excluir projeto com regeneração **na fila** agora cancela e exclui
      (antes: 409); com regeneração **rodando** continua 409

## Etapa 9 — correções do teste de capacidade
- [x] `WORKER_MEMORY_LIMIT_MB` padrão 8192 (4096 impedia vídeos de 60 min); limite de RAM do worker no Compose 6 GB
- [x] Falha de memória (`bad_alloc`, `MemoryError`) vira `resource_limit`, com mensagem clara e sem consumir tentativa

## Etapa 9 — Lote 3 (retenção e exclusão)
- [x] Projeto sem vídeo há mais de 24 h excluído; recente mantido
- [x] Original, transcrição e candidatos apagados 7 dias após a conclusão; cortes mantidos; regenerar → 409
      `source_expired`; projeto com 6 dias intacto
- [x] Projeto inteiro excluído 30 dias após a conclusão; falho/cancelado pela última atividade; 29 dias mantido;
      registro de consumo preservado sem vínculo
- [x] Datas de expiração no status do projeto e na página do projeto
- [x] Projeto com job ativo ou travado é pulado e excluído na rodada seguinte
- [x] Storage fora do ar: nada apagado do banco; próxima rodada conclui (exclusão retomável)
- [x] Arquivos órfãos com mais de 24 h apagados; arquivos com registro no banco preservados
- [x] Detalhe técnico de erros esvaziado após 30 dias (código mantido); auditoria com mais de 365 dias apagada
- [x] Retenção idempotente; 3 rodadas simultâneas → uma única exclusão
- [x] Corrida retenção × regeneração sem deadlock (corrigido: ordem de travas projeto → corte)
- [x] Exclusão de conta: arquivos e dados apagados; dados de outro usuário intactos; consumo sem vínculo; auditoria
      sem dados pessoais; token antigo recebe 403; login removido no Supabase (servidor de teste); sem chave →
      aviso; erro no Supabase não desfaz a exclusão; processamento rodando → 409; job na fila cancelado;
      storage fora do ar → 503 sem apagar nada
- [x] Página `/privacidade` com prazos e suboperadores lidos da configuração; `/conta` com exclusão confirmada por
      digitação; testes de navegador, acessibilidade e celular
- [ ] Exclusão de login com Supabase real — depende da chave `service_role` (Lote 7)
- [ ] Texto jurídico da política — depende de revisão; contato de suporte (`SUPPORT_EMAIL`) a definir

## Etapa 9 — Lote 4 (convites, limites, custo, administração)
- [x] Beta fechado: só convidados (configuração, domínio ou comando) usam a API; sem diferenciar maiúsculas;
      token sem e-mail recusado; revogação vale para perfis existentes e para e-mails da configuração
- [x] Frontend em modo beta: sem cadastro aberto, "acesso por convite", não convidado vê a mensagem da API
- [x] Período da cota do beta atravessa a virada do mês (consumo anterior ao período não conta)
- [x] 1 processamento por vez (429 `concurrent_limit`); 20 regenerações por dia (429 `regeneration_limit`)
- [x] Reserva de custo de IA: teto por usuário (402 `cost_limit`), reserva liberada ao terminar, custo real medido
      substitui a reserva; teto global (402 `beta_budget_exhausted`) com pedidos simultâneos de usuários diferentes
      → exatamente um aceito
- [x] Administração por linha de comando: convidar/revogar/listar, usuários, suspender/reativar, cota, período,
      relatório (texto e JSON); comandos auditados com o operador
- [ ] Convite por e-mail do Supabase (`--enviar-email`) — testado contra servidor local; **não validado com
      Supabase real**
- [ ] Recalibrar `LLM_RESERVE_USD_PER_MINUTE` e os tetos com o custo real medido (Lote 7)

## Etapa 9 — Lote 5 (monitoramento e qualidade)
- [x] `/health/ready`: 200 com tudo no ar; 503 sem worker, com Redis ou armazenamento fora
- [x] Alertas: falhas, fila, sem worker, Redis, custo ≥ 80% do teto, jobs perdidos, retenção e manutenção;
      formatos Slack/Discord/JSON; sem repetição na janela (Redis ou memória); sem dados pessoais
- [x] Relatório diário enviado uma vez por intervalo, sem e-mails; inclui avaliações dos cortes
- [x] Erros do navegador e relatórios de CSP registrados sem tokens nem consulta da URL; limite por IP (429);
      corpo grande recusado (413)
- [x] Avaliação "útil / não útil" por corte (a última vale), persistida e com isolamento por usuário
- [x] Frontend: cabeçalhos de segurança e CSP Report-Only; **nenhuma violação de CSP no fluxo completo** (com
      teste de controle que prova que o detector funciona)
- [x] `/docs` desligado em produção; `nosniff`/`no-store` na API
- [x] Barreira: build com modo de login de teste falha sem `AUTOCLIP_ALLOW_TEST_AUTH=1`
- [x] Páginas de erro e "não encontrada" em português; erros reportados à API
- [x] Sessão expirada (401) volta ao login com aviso; não convidado (403) continua vendo a mensagem
- [x] "Vídeos processados" pelo total real; "Nota estimada" na galeria
- [x] Acessibilidade (axe) também na página do projeto com a galeria
- [x] `npm audit`: 0 vulnerabilidades (22/11/2026)
- [ ] Canal de alertas real (Slack/Discord) — depende de você; testado contra servidor local
- [ ] Ativar o bloqueio da CSP — depois do beta, após período sem violações

## Etapa 9 — Lote 6 (implantação)
- [x] `deploy/docker-compose.prod.yml` válido (9 serviços; `worker2` por perfil); Redis com persistência; limites
      de memória do worker; logs com rotação; `migrate` aplica migrações e configura o bucket
- [x] Caddy com HTTPS automático, HSTS, limite de 1 MB para a API e timeout de 300 s para a validação do upload
- [x] Backup diário com conferência do arquivo e retenção de 7 dias; **restauração testada com dados reais**
      (10 tabelas, 1.342 trechos de transcrição, conteúdo idêntico por checksum)
- [x] Roteiro de implantação e verificação antes de convidar ([IMPLANTACAO.md](IMPLANTACAO.md))
- [x] Imagens construídas; versões fixadas existem; **pilha de produção executada localmente** (Caddy com HTTPS
      interno, 10 serviços, migrate, prontidão, `/docs` 404, cabeçalhos, convite, upload, validação, transcrição no
      worker com limites, administração, backup, alerta de worker parado)
- [x] Suíte completa do backend (246) **dentro da imagem de produção** (FFmpeg 7.1, Python 3.12)
- [x] Playwright (30 testes, desktop e celular) **contra a pilha de produção** pelo HTTPS do Caddy — com frontend
      em modo de autenticação de teste e avaliador de teste no lugar do Claude; transcrição (Whisper `small`),
      renderização e regeneração reais no worker de produção
- [ ] Certificado real e pilha na VM com provedores reais
- [ ] Substituir a imagem MinIO do `docker-compose.yml` de desenvolvimento (indisponível no Docker Hub) — aguarda aprovação
- [ ] Cópia dos backups para fora da VM — recomendação; depende do provedor

## Etapa 9 — Lote 7 (validação real e liberação)
- [x] `python -m app.validar armazenamento|supabase|claude|tudo` com APROVADO/REPROVADO/PULADO e evidências;
      código de saída 1 se algo reprovar; objetos temporários apagados
- [x] Contra o emulador S3, o validador **reprova** o que o emulador não aplica (expiração, limite de tamanho e de
      tipo no upload, CORS de outras origens) — prova de que detecta; Supabase e Claude testados contra servidores
      locais (inclui cálculo do custo e da reserva sugerida)
- [x] Roteiro do beta ([BETA.md](BETA.md)): checklist de liberação, acompanhamento, critérios de pausa, incidentes,
      mensagem aos convidados, encerramento
- [ ] Executar `app.validar` com armazenamento, Supabase e Claude reais — depende das chaves e da VM
- [ ] Decisão sobre os dados no fim do beta (apagar ou migrar)

## Critérios de aceitação gerais (seção 8)
- [x] Upload de MP4 válido (API + storage S3 emulado)
- [x] Upload inválido rejeitado com mensagem compreensível
- [x] Isolamento entre usuários (projetos)
- [x] Vídeo de teste transcrito com timestamps coerentes
- [~] Seleção por IA devolve candidatos válidos e dentro da duração — validação e seleção testadas; avaliação pelo
      modelo real pendente de chave
- [x] Corte reproduzível e baixável em MP4
- [x] Falhas registradas sem travar o sistema
- [x] Requisição repetida não duplica processamento nem cobrança
- [x] Testes E2E Playwright
