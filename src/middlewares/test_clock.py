from fastapi import FastAPI, Request

from constants import TEST_CLOCK_ENABLED, TEST_CLOCK_HEADER
from utils.clock import parse_instant, set_fixed_now


def register_test_clock_middleware(application: FastAPI) -> None:
    """Deixa o teste fixar o relógio do servidor, só fora de produção.

    A política tem dois relógios. O de EVENTO é o transaction_date, que o
    cliente manda. O de RECEBIMENTO é o do servidor: ele decide a
    admissibilidade (transaction_date entre agora − 24h e agora + 5 min),
    o corte do histórico e o prazo do 2FA.

    Os cenários da matriz de testes têm histórico de meses — 30 Pix em
    agosto, uma confirmação de 2FA às 10:03 — e cada evento desse
    histórico precisa ENTRAR pela API, como manda o teste de caixa-preta.
    Com o relógio real, nenhum Pix de agosto passaria na admissibilidade.

    Por isso, com APP_ENV local ou test, o cabeçalho X-Test-Clock (um
    ISO 8601 com offset) vira o "agora" desta requisição. Em production a
    flag fica desligada e o cabeçalho é ignorado: ninguém de fora escolhe
    o relógio do motor. Valor fora do formato também é ignorado.
    """

    @application.middleware("http")
    async def apply_test_clock(request: Request, call_next):
        fixed_now = None

        if TEST_CLOCK_ENABLED:
            raw_value = request.headers.get(TEST_CLOCK_HEADER)

            if raw_value is not None:
                fixed_now = parse_instant(raw_value)

        set_fixed_now(fixed_now)

        try:
            return await call_next(request)
        finally:
            set_fixed_now(None)
