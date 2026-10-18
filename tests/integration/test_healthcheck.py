from tests.utils import INTERNAL_TOKEN
from tests.utils.requisition import ClientRequisition


# As frases em português que o cliente lê no campo "translation" das
# respostas de erro. Estão escritas aqui à mão pelo mesmo motivo dos
# cabeçalhos acima: o teste cobra o contrato pela rede, sem importar
# nada de src/.
#
# O que elas defendem é o ACENTO. Até 2026-08-20 o QIException raspava
# todo acento antes de responder ("normalize NFKD" + encode ASCII), e a
# fonte acentuada chegava no cliente sem cedilha nem til. Quem repuser
# aquela linha deixa o teste abaixo vermelho, nomeando o campo.
FORBIDDEN_TRANSLATION = "Requisição precisa ser interna"
NOT_FOUND_TRANSLATION = "O resource solicitado não pode ser encontrado, mas pode estar disponível no futuro. Requests subsequentes do cliente são permitidos."


class TestHealthCheck:
    def test_home(self):
        response = ClientRequisition.send("GET", "/")
        assert response.response_status == 200

    def test_health_check(self):
        response = ClientRequisition.send("GET", "/health_check")
        assert response.response_status == 204

    def test_no_token(self):
        response = ClientRequisition.send("PUT", "/sample")
        assert response.response_status == 403
        assert response.response_json["code"] == "QIT000002"

    def test_sink(self):
        response = ClientRequisition.send(
            "PUT",
            "/sample",
            headers={"INTERNAL-TOKEN": INTERNAL_TOKEN},
        )
        assert response.response_status == 404
        assert response.response_json["code"] == "QIT000404"

    def test_method_not_allowed(self):
        response = ClientRequisition.send("PUT", "/health_check")
        assert response.response_status == 405
        assert response.response_json["code"] == "QIT000405"

    def test_error_translation_keeps_accents(self):
        """O acento da fonte chega no cliente — nas duas portas de erro da API.

        São dois caminhos diferentes, e por isso são duas requisições num
        teste só. O 403 nasce DENTRO do middleware de token, que devolve a
        resposta com a requisição ainda a caminho da rota; o 404 nasce num
        exception handler, bem mais pra dentro. Um só não prova o outro.

        O que se defende aqui é o campo "translation" em português de
        verdade: JSON é UTF-8, o acento cabe nele, e a API não tem por que
        raspar cedilha e til de um texto escrito pra brasileiro ler.
        """
        response = ClientRequisition.send("PUT", "/sample")
        assert response.response_status == 403
        assert response.response_json["code"] == "QIT000002"
        assert response.response_json["translation"] == FORBIDDEN_TRANSLATION

        response = ClientRequisition.send(
            "PUT",
            "/sample",
            headers={"INTERNAL-TOKEN": INTERNAL_TOKEN},
        )
        assert response.response_status == 404
        assert response.response_json["code"] == "QIT000404"
        assert response.response_json["translation"] == NOT_FOUND_TRANSLATION
