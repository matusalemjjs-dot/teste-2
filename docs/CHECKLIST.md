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
- [ ] Envio pelo navegador (CORS) — Etapa 8

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

## Critérios de aceitação gerais (seção 8)
- [x] Upload de MP4 válido (API + storage S3 emulado)
- [x] Upload inválido rejeitado com mensagem compreensível
- [x] Isolamento entre usuários (projetos)
- [x] Vídeo de teste transcrito com timestamps coerentes
- [~] Seleção por IA devolve candidatos válidos e dentro da duração — validação e seleção testadas; avaliação pelo
      modelo real pendente de chave
- [x] Corte reproduzível e baixável em MP4
- [x] Falhas registradas sem travar o sistema
- [x] Requisição repetida não duplica processamento (cobrança: Etapa 7)
- [ ] Testes E2E Playwright (Etapa 8)
