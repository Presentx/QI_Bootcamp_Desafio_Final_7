import os

from fastapi import Response, status
from fastapi.responses import JSONResponse

from constants import SERVICE_NAME


class HealthCheckResource:
    """As duas rotas que respondem sem token: quem é este serviço, e se ele está vivo."""

    def on_get_home(self) -> JSONResponse:
        home = {"service": SERVICE_NAME, "id": str(os.getpid())}

        return JSONResponse(content=home, status_code=status.HTTP_200_OK)

    def on_get_health_check(self) -> Response:
        # 204 quer dizer "deu tudo certo e não tenho nada a dizer".
        # Por isso a resposta não tem corpo nenhum: é um `Response`
        # pelado, e não um JSONResponse.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
