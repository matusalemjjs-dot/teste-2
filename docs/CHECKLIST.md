# Checklist de validação

Legenda: [x] implementado e coberto por teste automatizado executado · [ ] pendente

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

## Critérios de aceitação gerais (seção 8)
- [x] Upload de MP4 válido (API + storage S3 emulado)
- [x] Upload inválido rejeitado com mensagem compreensível
- [x] Isolamento entre usuários (projetos)
- [ ] Transcrição com timestamps coerentes (Etapa 4)
- [ ] Seleção por IA com candidatos válidos (Etapa 5)
- [ ] Corte reproduzível e baixável em MP4 (Etapa 6)
- [x] Falhas registradas sem travar o sistema
- [x] Requisição repetida não duplica processamento (cobrança: Etapa 7)
- [ ] Testes E2E Playwright (Etapa 8)
