from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from controllers import PixTransactionController
from utils.schema_handler import SchemaHandler


class PixTransactionResource:
    """A porta de entrada HTTP do motor antifraude.

    Confere o corpo (o decorator do schema), chama o controller e devolve
    o que ele respondeu, com o status certo. Nenhuma regra mora aqui —
    nem a de idempotência: quem sabe se o id é novo é o controller, que
    devolve `created`; o resource só traduz isso em 201 ou 200, que é
    vocabulário de HTTP.

    Uma rejeição antifraude NÃO é erro: é 201 com status rejected. A
    análise foi feita e gravada com sucesso; o resultado dela é que foi
    "não".
    """

    @SchemaHandler.validate("post_pix_transaction.json")
    def on_post(self, payload: dict) -> JSONResponse:
        controller = PixTransactionController()
        analysis = controller.analyze(payload)

        status_code = http_status.HTTP_200_OK
        if analysis["created"]:
            status_code = http_status.HTTP_201_CREATED

        return JSONResponse(content=jsonable_encoder(analysis["response"]), status_code=status_code)

    def on_get_by_id(self, transaction_id: str) -> JSONResponse:
        controller = PixTransactionController()
        analysis = controller.get_by_transaction_id(transaction_id)

        return JSONResponse(content=jsonable_encoder(analysis), status_code=http_status.HTTP_200_OK)

    @SchemaHandler.validate("put_two_factor.json")
    def on_put_two_factor(self, transaction_id: str, payload: dict) -> JSONResponse:
        controller = PixTransactionController()
        two_factor = controller.register_two_factor(transaction_id, payload["result"])

        return JSONResponse(content=jsonable_encoder(two_factor), status_code=http_status.HTTP_200_OK)
