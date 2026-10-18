from fastapi import FastAPI, Request

from utils.request_context import REQUEST_ID_HEADER, build_request_id, set_request_id


def register_request_context_middleware(application: FastAPI) -> None:
    """Dá um nome próprio a cada requisição, do primeiro byte ao último.

    Este é o middleware mais curto do projeto e o que mais economiza
    tempo no dia ruim. Pense na cena: alguém diz "deu erro por volta das
    14h30". Você abre o log e encontra mil linhas parecidas, de mil
    requisições diferentes, embaralhadas — porque a API atende várias ao
    mesmo tempo, e o log é um só.

    Com o identificador, toda linha nascida da MESMA requisição carrega
    o MESMO nome. Achar uma agulha vira um comando:

        docker compose logs api | grep 8f3c1e42

    Três decisões deste arquivo valem a leitura:

    • **Ele não pula rota nenhuma.** Os outros middlewares deixam passar
      as rotas de BYPASS_ENDPOINTS; este não tem exceção, porque não
      existe requisição que não mereça um nome — inclusive a que deu
      errado logo na porta.

    • **O identificador volta no cabeçalho da resposta.** Quem chamou
      recebe o número de protocolo e pode citá-lo ao abrir um chamado:
      "deu erro, e o id era este". Sem isso, ele teria o problema e você
      teria o log, e ninguém conseguiria juntar os dois.

    • **Ele guarda o valor num ContextVar** (em src/utils/request_context.py)
      em vez de passar o identificador de parâmetro em parâmetro por
      todas as camadas até o repository. O logger lê de lá sozinho, e
      nenhuma função do projeto precisou ganhar um argumento a mais.
    """

    @application.middleware("http")
    async def create_request_context(request: Request, call_next):
        request_id = build_request_id(request.headers.get(REQUEST_ID_HEADER))
        set_request_id(request_id)

        response = await call_next(request)

        response.headers[REQUEST_ID_HEADER] = request_id

        return response
