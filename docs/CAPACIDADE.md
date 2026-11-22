# Teste de capacidade — beta fechado (Lote 2)

Executado em 22/11/2026, num contêiner Linux de **4 vCPU e 16 GB de RAM** (perfil próximo da VM proposta para
o beta). Dados brutos em [`docs/capacidade/`](capacidade/). Script: `backend/tests/capacidade.py`.

## Método

- **Vídeo**: 59 min 50 s, 1920x1080, 30 fps, H.264 + AAC, 1,13 GB, com fala contínua em português gerada por
  espeak-ng (só como dado de teste).
- **Pilha**: workers reais (fork, limites por job), Postgres, Redis, armazenamento S3 **emulado** (moto).
- **Configuração de produção**: Whisper `small`, beam 5, 2 threads; FFmpeg 2 threads; preset `veryfast`;
  timeout de job 3.600 s.
- **Substituição**: só o avaliador de IA (o tempo do Claude depende da rede; será medido no Lote 7).

## Resultados

| Cenário | Resultado | Tempo por vídeo | Transcrição | Renderização (10 cortes) | RAM real (pico) | Memória livre mínima | Disco temporário | Carga |
|---|---|---|---|---|---|---|---|---|
| 1 worker, limite 4 GB (padrão atual) | **Falhou** (`std::bad_alloc` na transcrição, aos 14 s) | — | — | — | 1,4 GB | 12,9 GB | 1,2 GB | — |
| 1 worker, limite 8 GB | Sucesso, 10 cortes de 34 a 53 s | 11 min 50 s | 9 min 5 s | 2 min 43 s | 4,3 GB | 10,5 GB | 1,2 GB | 3,6 |
| 2 workers, 2 vídeos simultâneos, limite 8 GB | Sucesso nos dois, 10 cortes cada | 12 min 50 s | 9 min 6 s | 3 min 40 s | 8,3 GB (soma) | 6,2 GB | 2,5 GB | 7,6 |

Validação do upload (cópia imutável + FFprobe + decodificação), por vídeo de 1,13 GB: 120 a 165 s. Medição
isolada: **cópia no emulador 103 s**, análise 3,8 s (1,0 s em arquivo local). A cópia num S3/R2 real é feita
pelo provedor e precisa ser medida no Lote 7.

## Achados

1. **Crítico — vídeos de 60 min não são processados com o padrão atual (`WORKER_MEMORY_LIMIT_MB=4096`).**
   O limite usa `RLIMIT_AS`, que conta memória **virtual**. O ONNX Runtime (filtro de voz do faster-whisper) e
   o CTranslate2 reservam muito espaço de endereçamento: pico virtual de 7,5 GB para 4,3 GB de memória real.
   Com 8 GB o processamento completo funciona.
2. **A falha de memória é classificada como `transcription_failed`**, com mensagem genérica, e consome uma
   tentativa do usuário; deveria ser `resource_limit` (falha de infraestrutura).
3. **A validação do upload é síncrona e pode passar de 60 s** com o emulador. Proxies com timeout padrão de 60 s
   cortariam a requisição. Num S3 real deve ser bem mais rápida, mas isso precisa ser medido.
4. **Dois workers em 4 vCPU funcionam**: a transcrição não perde velocidade (cada worker usa 2 threads); a
   renderização fica 35% mais lenta; a RAM somada chega a 8,3 GB.
5. **Folga de tempo**: cerca de 13 min por vídeo de 60 min, contra o timeout de 60 min por job.

## Capacidade para o beta (estimativa)

- **1 worker**: cerca de 5 h de vídeo processadas por hora. O pior caso do beta (10 usuários × 180 min = 30 h
  de vídeo) consome cerca de 6 h de worker em 14 dias.
- **Espera na fila**: com 1 worker, se 3 pessoas enviarem vídeos de 60 min juntas, a terceira espera cerca de
  25 min. Com 2 workers, cerca de 13 min.
- **Tempo real esperado por vídeo de 60 min**: 12 a 13 min medidos, mais a análise pelo Claude (estimada em
  2 a 5 min: cerca de 5 requisições de 40 janelas). Total estimado de 14 a 18 min.

## Recomendação de VM

- **4 vCPU, 16 GB de RAM, 60 GB de SSD**, com armazenamento de objetos gerenciado fora da VM.
- **1 worker** no início (como decidido), com 2 workers testados e prontos para ativar se a espera na fila
  passar de 15 min.
- **Não usar VM de 8 GB**: com 1 worker sobrariam cerca de 3 GB para Postgres, Redis, API, proxy e sistema; com
  2 workers não cabe.
- **Timeout do proxy de pelo menos 300 s** na rota de validação do upload, até haver medição com o
  armazenamento real.

## Limitações do teste

- Fala sintética contínua: vídeos reais têm pausas (o filtro de voz descarta silêncio e acelera a
  transcrição) e câmeras com taxa de bits maior (a decodificação na renderização pode ficar mais lenta).
- Armazenamento emulado: tempos de upload, cópia e download não representam S3/R2.
- O emulador guarda os arquivos na memória do processo de teste, o que reduz a "memória livre" medida; os
  workers não foram afetados.
- O tempo da análise pelo Claude não foi medido (Lote 7).
