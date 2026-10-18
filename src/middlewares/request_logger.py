import time

from fastapi import FastAPI, Request

from constants import BYPASS_ENDPOINTS
from utils.logger import get_logger


logger = get_logger(__name__)


def register_request_logger_middleware(application: FastAPI) -> None:
    """Escreve no log toda requisição que entra e toda resposta que sai.

    Quando algo der errado em produção, é esta linha de log que conta
    a história: qual rota, qual status, quanto tempo demorou — e, no
    colchete que o logger acrescenta sozinho, o identificador da
    requisição, que junta as duas linhas (a de entrada e a de saída) e
    tudo o que aconteceu entre elas.

    Duas coisas que este middleware NÃO faz, de propósito:

    • **Não escreve o corpo da requisição no log.** É tentador, e é
      assim que dado sensível vaza: senha, documento, número de cartão
      — tudo o que o cliente mandou ficaria gravado em texto puro num
      arquivo que muita gente lê e que ninguém trata como confidencial.
      Se um dia você precisar logar o corpo, logue campo escolhido a
      dedo, nunca o objeto inteiro.

    • **Não fala das rotas de BYPASS_ENDPOINTS.** O Docker consulta o
      /health_check a cada três segundos; sem esta linha, o log seria
      quase só isso.
    """

    @application.middleware("http")
    async def log_request(request: Request, call_next):
        if request.url.path in BYPASS_ENDPOINTS:
            return await call_next(request)

        # O que veio depois do "?" no endereço entra no log de entrada:
        # é metade do pedido, e sem ele a linha "GET /sample_entities"
        # não diz qual página alguém pediu.
        #
        # E fica a lição pelo avesso: se a query string vai parar no
        # log, ela NÃO é lugar para segredo. Um token na URL acaba
        # gravado aqui, no histórico do navegador e no log de todo proxy
        # do caminho. Segredo viaja em cabeçalho, como o INTERNAL-TOKEN.
        requested_path = request.url.path
        if request.url.query:
            requested_path = f"{requested_path}?{request.url.query}"

        started_at = time.perf_counter()
        logger.info(f"ENTROU {request.method} {requested_path}")

        response = await call_next(request)

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(f"SAIU {response.status_code} {request.method} {request.url.path} - {elapsed_ms:.1f} ms")

        return response
