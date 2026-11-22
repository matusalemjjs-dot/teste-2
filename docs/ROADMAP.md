# Roteiro incremental do MVP

Cada etapa termina com testes automatizados executados e um relatório do que foi feito.

| Etapa | Escopo | Módulos | Estado |
|---|---|---|---|
| 1 | Fundação do backend: configuração, logs, modelo de dados completo + migração Alembic, autenticação Supabase (JWT), `/health`, `/me`, CRUD de projetos com isolamento e máquina de estados | M01 (backend), M04 | **Concluída** |
| 2 | Armazenamento S3 privado (MinIO local), `upload-url`, `finalize-upload`, validação com FFprobe (tipo real, duração ≤ 60 min, integridade), limites configuráveis | M03 | **Concluída** |
| 3 | Fila Redis/RQ, `process` idempotente, `processing_jobs`, worker com limites de tempo/recursos, limpeza de temporários, progresso | M04, pipeline 4/13/14 | **Concluída** |
| 4 | Extração de áudio (FFmpeg) e adaptador de transcrição substituível (Whisper local / serviço externo) | M05 | **Concluída** |
| 5 | Janelas candidatas 15–60 s, adaptador de LLM com saída estruturada, validação de timestamps, remoção de sobreposição, top 10 | M06 | **Concluída** (validação com o modelo real pendente) |
| 6 | Renderização vertical 1080x1920 (crop central / fundo desfocado), legendas, miniaturas, `clips`, `download-url`, regenerar corte | M07, M08 (API) | **Concluída** |
| 7 | Consumo: `usage_ledger` idempotente, quotas, rate limiting | M09 | **Concluída** |
| 8 | Frontend Next.js: login/cadastro/recuperação (Supabase), dashboard, upload, galeria; testes Playwright | M01, M02, M08 | **Concluída** (login real pendente de projeto Supabase) |
| 9 | Painel administrativo (falhas, filas, consumo, custos), retenção/exclusão automática (LGPD) | M10 | Próxima |
