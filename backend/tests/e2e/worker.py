"""Worker para os testes E2E: igual ao de produção, mas com o avaliador de teste no lugar do Claude.

Fica na pasta de testes de propósito: o código do produto não tem avaliador simulado.
"""

from app.analysis import llm
from tests.test_analysis import FakeScorer

llm.get_scorer = lambda settings: FakeScorer()

if __name__ == "__main__":
    from app.worker.run import main

    main()
