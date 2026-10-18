from tests.utils import PayloadGenerator, RandomGenerator, RequestGenerator
from tests.utils.requisition import ClientRequisition
from tests.utils.time_utils import at

NOW = "2026-10-17 14:00:01"


def post(payload: dict):
    return RequestGenerator.POST_pix_transaction(payload, clock=at(NOW))


class TestPixValidation:
    """As três etapas de validação (seção 2) e o acesso.

    Estrutura torta é 400 QIT000001; valor bem formado e impossível é 422.
    Nas duas, nada é gravado: o GET do id depois responde 404.
    """

    def test_creates_with_the_contract_example(self):
        payload = PayloadGenerator.create_pix_payload(
            amount=150_000,
            transaction_date="2026-10-17T22:15:00-03:00",
            payer_document="123.456.789-09",
            destination_opening_date="2026-10-10T09:00:00-03:00",
            destination_document="987.654.321-00",
        )

        status, response = RequestGenerator.POST_pix_transaction(payload, clock="2026-10-17T22:15:01-03:00")
        assert status == 201
        assert set(response.keys()) == {"key", "status", "metadata"}
        assert response["status"] in ("approved", "two_factor_required", "rejected")
        assert set(response["metadata"].keys()) == {"score", "score_band", "decision_basis", "policy_version"}
        assert response["metadata"]["policy_version"] == "6.0.0"

    def test_requires_internal_token(self):
        payload = PayloadGenerator.create_pix_payload()
        response = ClientRequisition.send("POST", "/pix/transaction", payload=payload)
        assert response.response_status == 403
        assert response.response_json["code"] == "QIT000002"

    def test_refuses_malformed_structure(self):
        """Cada volta derruba um campo e mantém o resto válido."""
        malformed = [
            ("id", "pix 1 com espaço no meio"),
            ("id", "x" * 65),
            ("amount", 0),
            ("amount", 1_000_000_001),
            ("amount", "150000"),
            ("amount", 10.5),
            ("transaction_date", "2026-10-17T14:00:00"),
            ("transaction_date", "17/10/2026 14:00"),
            ("transaction_date", "2026-02-30T14:00:00-03:00"),
        ]

        for field, value in malformed:
            payload = PayloadGenerator.create_pix_payload(transaction_date=at("2026-10-17 14:00"))
            payload[field] = value

            status, response = post(payload)
            assert status == 400, f"{field}={value!r} was accepted: {response}"
            assert response["code"] == "QIT000001"

    def test_refuses_malformed_nested_fields(self):
        cases = [
            lambda p: p["source_account"]["owner"].update(document_number="123.456.789"),
            lambda p: p["destination_account"].update(participant="6070119"),
            lambda p: p["destination_account"].update(branch="12a"),
            lambda p: p["source_account"].update(account_number="1" * 21),
            lambda p: p["source"].update(device_id=""),
            lambda p: p["source"].update(device_id="d" * 129),
            lambda p: p["source"].update(channel="c" * 21),
            lambda p: p["source"].update(ip="999.1.1.1"),
            lambda p: p["source"].update(ip="not-an-ip"),
            lambda p: p["source"].update(geolocation={"latitude": -23.5}),
            lambda p: p["source"].update(geolocation={"latitude": 91, "longitude": 0}),
            lambda p: p["source"].update(geolocation={"latitude": 0, "longitude": -181}),
            lambda p: p["source"].update(extra="campo a mais"),
        ]

        for index, mutate in enumerate(cases):
            payload = PayloadGenerator.create_pix_payload(transaction_date=at("2026-10-17 14:00"))
            mutate(payload)

            status, response = post(payload)
            assert status == 400, f"case {index} was accepted: {response}"
            assert response["code"] == "QIT000001"

    def test_accepts_ipv6_cnpj_and_trimmed_id(self):
        transaction_id = RandomGenerator.generate_transaction_id()
        payer_document = RandomGenerator.generate_cnpj()
        payload = PayloadGenerator.create_pix_payload(
            transaction_id=f"  {transaction_id}  ",
            transaction_date=at("2026-10-17 14:00"),
            payer_document=payer_document,
            ip="2001:db8::1",
        )

        status, response = post(payload)
        assert status == 201, response

        status, response = RequestGenerator.GET_pix_transaction(transaction_id)
        assert status == 200
        assert response["transaction"]["transaction_id"] == transaction_id
        assert response["transaction"]["payer_document"] == payer_document.replace(".", "").replace("/", "").replace("-", "")
        assert response["transaction"]["payer_type"] == "PJ"

    def test_refuses_invalid_check_digits(self):
        """Máscara certa, dígitos verificadores errados: 422 QIT001003 — pagador ou recebedor."""
        for field in ("source_account", "destination_account"):
            payload = PayloadGenerator.create_pix_payload(transaction_date=at("2026-10-17 14:00"))
            payload[field]["owner"]["document_number"] = "111.222.333-44"

            status, response = post(payload)
            assert status == 422, response
            assert response["code"] == "QIT001003"
            assert "111.222.333-44" not in response["description"]

            status, _response = RequestGenerator.GET_pix_transaction(payload["id"])
            assert status == 404

    def test_refuses_account_opened_after_transaction(self):
        for field in ("source_account", "destination_account"):
            payload = PayloadGenerator.create_pix_payload(transaction_date=at("2026-10-17 14:00"))
            payload[field]["opening_date"] = at("2026-10-17 14:00:01")

            status, response = post(payload)
            assert status == 422, response
            assert response["code"] == "QIT001005"

    def test_admissibility_boundaries(self):
        """Teste 18i: recebimento + 5 min aceito; + 5 min 1 s, 422; − 24h aceito; − 24h 1 s, 422."""
        received = "2026-10-17 14:00:00"
        cases = [
            ("2026-10-17 14:05:00", 201),
            ("2026-10-17 14:05:01", 422),
            ("2026-10-16 14:00:00", 201),
            ("2026-10-16 13:59:59", 422),
        ]

        for transaction_date, expected_status in cases:
            payload = PayloadGenerator.create_pix_payload(transaction_date=at(transaction_date))
            status, response = RequestGenerator.POST_pix_transaction(payload, clock=at(received))
            assert status == expected_status, f"{transaction_date}: {response}"

            if expected_status == 422:
                assert response["code"] == "QIT001004"

    def test_get_unknown_id(self):
        status, response = RequestGenerator.GET_pix_transaction("pix-que-nao-existe")
        assert status == 404
        assert response["code"] == "QIT001001"

    def test_zero_zero_geolocation_is_absent(self):
        payload = PayloadGenerator.create_pix_payload(
            transaction_date=at("2026-10-17 14:00"), geolocation={"latitude": 0, "longitude": 0}
        )
        status, _response = post(payload)
        assert status == 201

        status, response = RequestGenerator.GET_pix_transaction(payload["id"])
        assert response["transaction"]["latitude"] is None
        assert response["transaction"]["longitude"] is None
