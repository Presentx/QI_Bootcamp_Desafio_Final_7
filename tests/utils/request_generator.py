from os import environ

from tests.utils.requisition import ClientRequisition, BaseConnectorResponse


INTERNAL_TOKEN = environ.get("INTERNAL_TOKEN", "default_token")

# O cabeçalho do relógio de teste: o "agora" do servidor nesta requisição.
# Só vale com a API em APP_ENV local ou test (o padrão do compose).
TEST_CLOCK_HEADER = "X-Test-Clock"


def build_headers(clock: str = None) -> dict:
    headers = {"INTERNAL-TOKEN": INTERNAL_TOKEN}

    if clock is not None:
        headers[TEST_CLOCK_HEADER] = clock

    return headers


class RequestGenerator:
    @staticmethod
    def POST_pix_transaction(payload: dict, clock: str = None) -> BaseConnectorResponse:
        response = ClientRequisition.send(
            "POST",
            "/pix/transaction",
            payload=payload,
            headers=build_headers(clock),
        )
        return response.response_status, response.response_json

    @staticmethod
    def GET_pix_transaction(transaction_id: str, clock: str = None) -> BaseConnectorResponse:
        response = ClientRequisition.send(
            "GET",
            f"/pix/transaction/{transaction_id}",
            headers=build_headers(clock),
        )
        return response.response_status, response.response_json

    @staticmethod
    def PUT_two_factor(transaction_id: str, result: str, clock: str = None) -> BaseConnectorResponse:
        response = ClientRequisition.send(
            "PUT",
            f"/pix/transaction/{transaction_id}/two_factor",
            payload={"result": result},
            headers=build_headers(clock),
        )
        return response.response_status, response.response_json
