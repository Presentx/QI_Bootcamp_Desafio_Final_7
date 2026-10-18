import logging
import sys

from constants import APP_ENV, SERVICE_NAME
from utils.request_context import get_request_id


# O "[%(request_id)s]" é o identificador da requisição que gerou a linha
# — quem o preenche é o filtro logo abaixo. Uma linha sai assim:
#
#   2026-09-01 12:00:00 [INFO] bootcamp-api.middlewares.request_logger
#   [8f3c1e42-...] - ENTROU GET /sample_entities
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s [%(request_id)s] - %(message)s"


class RequestIdFilter(logging.Filter):
    """Carimba toda linha de log com o identificador da requisição.

    "Filter" é um nome infeliz do Python: esta classe não descarta nada
    — ela devolve True para tudo. O que ela faz é acrescentar um campo à
    linha antes de a formatação acontecer. É o gancho oficial do módulo
    de log para isso.

    A alternativa seria lembrar de escrever o identificador em cada
    chamada de logger.info do projeto. Uma hora alguém esquece, e é
    justamente na linha que você iria precisar.

    Quando não há requisição em andamento — a API subindo, por
    exemplo — o campo sai como "-", e nada quebra.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def setup_logging() -> None:
    """Liga o log da aplicação. Chamada uma vez, quando a API sobe."""
    level = logging.INFO
    if APP_ENV.upper() in ("LOCAL", "TEST"):
        level = logging.DEBUG

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))

    # O filtro fica no HANDLER, não num logger específico: assim toda
    # linha que passar por aqui ganha o campo, venha ela do nosso código
    # ou de qualquer biblioteca. Se ficasse num logger só, a primeira
    # linha vinda de outro lugar derrubaria a formatação.
    handler.addFilter(RequestIdFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = [handler]

    # O urllib3 fala MUITO em modo debug, e nada disso e nosso: sao
    # detalhes de conexao HTTP das bibliotecas que usamos.
    for biblioteca_falante in ["urllib3"]:
        logging.getLogger(biblioteca_falante).setLevel(logging.CRITICAL)


def get_logger(class_name: str) -> logging.Logger:
    """Devolve o log com o nome de quem está escrevendo.

    Assim, olhando a linha do log, você sabe de qual arquivo ela veio.
    """
    return logging.getLogger(f"{SERVICE_NAME}.{class_name}")
