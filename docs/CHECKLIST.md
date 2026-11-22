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

## Critérios de aceitação gerais (seção 8)
- [x] Upload de MP4 válido (API + storage S3 emulado)
- [x] Upload inválido rejeitado com mensagem compreensível
- [x] Isolamento entre usuários (projetos)
- [ ] Transcrição com timestamps coerentes (Etapa 4)
- [ ] Seleção por IA com candidatos válidos (Etapa 5)
- [ ] Corte reproduzível e baixável em MP4 (Etapa 6)
- [ ] Falhas registradas sem travar o sistema (Etapa 3)
- [ ] Requisição repetida não duplica processamento/cobrança (Etapas 3 e 7)
- [ ] Testes E2E Playwright (Etapa 8)
