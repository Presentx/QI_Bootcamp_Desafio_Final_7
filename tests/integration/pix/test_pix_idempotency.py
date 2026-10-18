from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from tests.utils import Destination, ObjectGenerator, Payer, PayloadGenerator, RequestGenerator
from tests.utils.time_utils import BRT, at


class TestPixIdempotency:
    """Idempotência só pelo id, resposta congelada e as travas (testes 14, 15 e 16)."""

    def test_repeated_id_returns_frozen_response_without_admissibility(self):
        """Teste 14: reenvio idêntico 26h depois — 200 com a resposta congelada."""
        payload = PayloadGenerator.create_pix_payload(transaction_date=at("2026-10-16 10:00"))

        status, first = RequestGenerator.POST_pix_transaction(payload, clock=at("2026-10-16 10:00:01"))
        assert status == 201
        assert first["status"] == "approved"

        status, second = RequestGenerator.POST_pix_transaction(payload, clock=at("2026-10-17 12:00"))
        assert status == 200
        assert second == first

    def test_same_id_with_different_content_keeps_the_original(self):
        """Teste 15: amount 150.000 → 150.001, e a data com outro offset: 200, nada muda."""
        payload = PayloadGenerator.create_pix_payload(amount=150_000, transaction_date="2026-10-17T22:15:00-03:00")

        status, first = RequestGenerator.POST_pix_transaction(payload, clock=at("2026-10-17 22:15:01"))
        assert status == 201

        payload["amount"] = 150_001
        status, second = RequestGenerator.POST_pix_transaction(payload, clock=at("2026-10-17 22:20"))
        assert status == 200
        assert second == first

        payload["amount"] = 150_000
        payload["transaction_date"] = "2026-10-18T01:15:00Z"
        status, third = RequestGenerator.POST_pix_transaction(payload, clock=at("2026-10-17 22:21"))
        assert status == 200
        assert third == first

        consultation = ObjectGenerator.analysis(payload["id"])
        assert consultation["transaction"]["amount"] == 150_000
        assert consultation["key"] == first["key"]

    def test_get_returns_frozen_response_and_reasoning(self):
        payload = PayloadGenerator.create_pix_payload(transaction_date=at("2026-10-17 14:00"))
        status, created = RequestGenerator.POST_pix_transaction(payload, clock=at("2026-10-17 14:00:01"))
        assert status == 201

        consultation = ObjectGenerator.analysis(payload["id"])
        assert consultation["key"] == created["key"]
        assert consultation["status"] == created["status"]
        assert consultation["metadata"] == created["metadata"]

        reasoning = consultation["reasoning"]
        for block in ("cut", "states_read", "floors", "rules", "attenuators", "score", "independent_rejection", "decision"):
            assert block in reasoning

        assert reasoning["cut"]["policy_version"] == "6.0.0"
        assert reasoning["cut"]["received_at"] == "2026-10-17T17:00:01.000Z"
        assert len(reasoning["floors"]) == 11

    def test_simultaneous_identical_requests_analyze_once(self):
        """Teste 16: requisições idênticas simultâneas — um 201 e um 200 com a mesma resposta."""
        payload = PayloadGenerator.create_pix_payload(transaction_date=at("2026-10-17 14:00"))

        def send(_index):
            return RequestGenerator.POST_pix_transaction(payload, clock=at("2026-10-17 14:00:01"))

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(send, range(4)))

        statuses = sorted(status for status, _response in results)
        assert statuses == [200, 200, 200, 201]

        keys = set(response["key"] for _status, response in results)
        assert len(keys) == 1

    def test_second_payment_of_the_night_sees_the_first(self):
        """Testes 16c-A e 16c-B: M30 alto, dois Pix de 60.000 às 21:00.

        A primeira passa (S11 +10, A3 −10, A4 −15: score 5, approved); a
        segunda enxerga a primeira na noite e F6 dispara (120.000 > 100.000).
        """
        payer, _y = ObjectGenerator.m30_payer(amount=500_000)
        friend = Destination()

        status, first, _first_id = payer.pay("2026-10-17 21:00", 60_000, friend, received="2026-10-17 21:00:01")
        assert status == 201
        assert first["status"] == "approved"
        assert first["metadata"]["score"] == 5

        status, second, second_id = payer.pay("2026-10-17 21:00", 60_000, friend, received="2026-10-17 21:00:02")
        assert status == 201
        assert second["status"] == "two_factor_required"
        assert second["metadata"]["decision_basis"] == "floor_2fa"
        assert second["metadata"]["score"] == 5
        assert ObjectGenerator.fired_floors(second_id) == ["F6"]

    def test_simultaneous_payments_of_the_same_payer_are_serialized(self):
        """Teste 16c com o relógio REAL do servidor: duas análises ao mesmo tempo.

        Sem o cabeçalho de relógio, o received_at é lido depois das travas —
        e é isso que garante que a segunda análise enxergue a primeira. PJ
        com conta de 2 dias (teto 500.000) manda dois Pix de 300.000 juntos:
        só um passa; o outro vê 600.000 na janela de 60 min (F9) e no dia
        (F11). Uma soma por pagador que não fosse travada aprovaria os dois.
        """
        now = datetime.now(timezone.utc)
        payer = Payer(kind="PJ", opened=(now - timedelta(days=2)).astimezone(BRT).strftime("%Y-%m-%d %H:%M"))
        supplier = Destination(kind="PJ")

        payloads = []
        for _payment in range(2):
            payload = payer.payload("2026-10-17 14:00", 300_000, supplier)
            payload["transaction_date"] = now.isoformat(timespec="milliseconds")
            payloads.append(payload)

        def send(payload):
            return RequestGenerator.POST_pix_transaction(payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, payloads))

        decisions = sorted((response["status"], response["metadata"]["decision_basis"]) for _status, response in results)
        assert decisions == [("approved", "score"), ("two_factor_required", "floor_2fa")]

        for status, _response in results:
            assert status == 201
