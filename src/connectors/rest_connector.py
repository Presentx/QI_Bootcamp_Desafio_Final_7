import json
import time
from abc import ABCMeta

import requests

from utils.logger import get_logger


class RestConnector(metaclass=ABCMeta):
    """O que todo connector tem em comum: como falar com outro serviço.

    Este arquivo é pra chamada HTTP o que o database.py é pro banco: o
    ÚNICO lugar que sabe COMO se fala com um serviço de fora. Quem
    precisa de outro serviço não escreve requests.get() no meio do
    código — cria um connector (subclasse desta) e chama
    um método com nome de gente.

    O que mora aqui, e por quê:

      • o ENDEREÇO vem de configuração, nunca escrito no código: o mesmo
        connector fala com o mock na sua máquina e com o serviço real em
        produção, mudando só uma variável de ambiente;

      • o TIMEOUT é obrigatório. Requisição sem prazo é uma forma de
        travar a SUA api: se o outro serviço parar de responder, cada
        pedido seu fica pendurado esperando pra sempre;

      • TODA ida e volta aparece no log, com o tempo que levou. Quando
        algo der errado entre dois serviços, são estas linhas que contam
        de que lado está o problema.
    """

    def __init__(self, class_name: str, base_url: str, timeout: int, internal_token: str) -> None:
        self.logger = get_logger(class_name)
        self.base_url = base_url
        self.timeout = timeout
        self.internal_token = internal_token

    def send(self, endpoint: str, method: str, payload: dict = None, headers: dict = None):
        if headers is None:
            headers = {}

        # Os serviços da QI se autenticam entre si do mesmo jeito que
        # esta API exige de quem a chama: o header INTERNAL-TOKEN.
        # Repare na simetria com src/middlewares/internal_token.py —
        # lá a gente confere o token de quem chega; aqui a gente manda
        # o token pra quem vamos chamar.
        headers["INTERNAL-TOKEN"] = self.internal_token

        url = f"{self.base_url}{endpoint}"

        self.logger.info(f"OUTGOING REQUEST {method} {url}")
        started_at = time.perf_counter()

        # Se o outro serviço demorar mais que o timeout, o requests
        # levanta um Timeout aqui — e isso é o comportamento desejado:
        # falhar rápido e com nome, em vez de esperar pra sempre.
        response = requests.request(
            method.upper(),
            url,
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        self.logger.info(f"INCOMING RESPONSE {response.status_code} {method} {url} - {elapsed_ms:.1f} ms")

        # O connector entrega a resposta COMO VEIO — status e corpo.
        # Ele não decide o que é erro: um 404 pode ser resposta válida
        # ("não achei") e um 500 pode merecer nova tentativa — quem sabe
        # disso é a regra de negócio, no controller. Status é informação
        # da resposta, não exceção.
        return BaseConnectorResponse(endpoint, method, response)


class BaseConnectorResponse:
    """A resposta do outro serviço, já desembrulhada.

    Guarda o que veio (status e corpo) e o que foi pedido (endpoint e
    método) — assim quem recebe a resposta lá na frente sabe contar a
    história inteira sem precisar do connector de novo.
    """

    def __init__(self, endpoint: str, method: str, response: requests.Response) -> None:
        self.endpoint = endpoint
        self.method = method
        self.status = response.status_code

        # Nem toda resposta tem corpo JSON (um 204, um proxy no meio do
        # caminho respondendo HTML). Corpo que não é JSON vira None em
        # vez de derrubar a requisição.
        self.json = None
        try:
            self.json = json.loads(response.content)
        except ValueError:
            pass
