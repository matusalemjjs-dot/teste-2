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

## Critérios de aceitação gerais (seção 8)
- [ ] Upload de MP4 válido (Etapa 2)
- [ ] Upload inválido rejeitado com mensagem compreensível (Etapa 2)
- [x] Isolamento entre usuários (projetos)
- [ ] Transcrição com timestamps coerentes (Etapa 4)
- [ ] Seleção por IA com candidatos válidos (Etapa 5)
- [ ] Corte reproduzível e baixável em MP4 (Etapa 6)
- [ ] Falhas registradas sem travar o sistema (Etapa 3)
- [ ] Requisição repetida não duplica processamento/cobrança (Etapas 3 e 7)
- [ ] Testes E2E Playwright (Etapa 8)
