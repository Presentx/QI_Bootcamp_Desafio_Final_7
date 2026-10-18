from tests.utils import Destination, ObjectGenerator, Payer, RequestGenerator
from tests.utils.time_utils import at, days


def two_factor_analysis(received: str) -> str:
    """Um Pix que o motor manda para 2FA: PF novo, 60.000 para destino desconhecido (F2)."""
    payer = Payer()
    status, response, transaction_id = payer.pay("2026-10-17 10:00", 60_000, Destination(), received=received)
    assert status == 201
    assert response["status"] == "two_factor_required"
    return transaction_id


def profile_18g():
    payer = Payer(opened="2026-09-27 10:00", device="D2")
    payer.seed_many(days("2026-10-01 12:00", 5), 100_000, Destination())
    return payer


def untrusted_device_two_factor(payer: Payer) -> str:
    """O 2FA das 10:00 do teste 20: 200.000 num dispositivo novo (F8)."""
    status, response, transaction_id = payer.pay("2026-10-17 10:00", 200_000, Destination(), device="D5", received="2026-10-17 10:00:00")
    assert response["status"] == "two_factor_required"
    return transaction_id


class TestPixTwoFactor:
    """A rota E1 (seção 8): PE → AU | NC, uma vez, dentro de 10 minutos."""

    def test_confirmation_flow(self):
        """Teste 21: 2FA recebido às 10:00, prazo 10:10."""
        transaction_id = two_factor_analysis(received="2026-10-17 10:00:00")

        status, response = RequestGenerator.PUT_two_factor(transaction_id, "confirmed", clock=at("2026-10-17 10:05"))
        assert status == 200
        assert response["status"] == "two_factor_required"
        assert response["two_factor"]["state"] == "AU"

        status, repeated = RequestGenerator.PUT_two_factor(transaction_id, "confirmed", clock=at("2026-10-17 10:20"))
        assert status == 200
        assert repeated == response

        status, response = RequestGenerator.PUT_two_factor(transaction_id, "declined", clock=at("2026-10-17 10:20"))
        assert status == 409
        assert response["code"] == "QIT001007"

        late = two_factor_analysis(received="2026-10-17 10:00:00")
        status, response = RequestGenerator.PUT_two_factor(late, "confirmed", clock=at("2026-10-17 10:11"))
        assert status == 409
        assert response["code"] == "QIT001008"

        on_the_deadline = two_factor_analysis(received="2026-10-17 10:00:00")
        status, response = RequestGenerator.PUT_two_factor(on_the_deadline, "confirmed", clock=at("2026-10-17 10:10:00"))
        assert status == 200

    def test_post_keeps_the_frozen_response_and_get_shows_the_result(self):
        transaction_id = two_factor_analysis(received="2026-10-17 10:00:00")
        status, original = RequestGenerator.GET_pix_transaction(transaction_id)

        status, _response = RequestGenerator.PUT_two_factor(transaction_id, "declined", clock=at("2026-10-17 10:03"))
        assert status == 200

        consultation = ObjectGenerator.analysis(transaction_id)
        assert consultation["status"] == "two_factor_required"
        assert consultation["key"] == original["key"]
        assert consultation["two_factor"]["state"] == "NC"
        assert consultation["two_factor"]["result"] == "declined"

    def test_unanswered_two_factor_expires(self):
        transaction_id = two_factor_analysis(received="2026-10-17 10:00:00")

        status, response = RequestGenerator.GET_pix_transaction(transaction_id, clock=at("2026-10-17 10:05"))
        assert response["two_factor"]["state"] == "PE"

        status, response = RequestGenerator.GET_pix_transaction(transaction_id, clock=at("2026-10-17 10:11"))
        assert response["two_factor"]["state"] == "NC"
        assert response["two_factor"]["result"] == "expired"

    def test_two_factor_only_for_two_factor_decisions(self):
        payer = Payer()
        _status, response, transaction_id = payer.pay("2026-10-17 10:00", 1_000, Destination())
        assert response["status"] == "approved"

        status, response = RequestGenerator.PUT_two_factor(transaction_id, "confirmed", clock=at("2026-10-17 10:01"))
        assert status == 409
        assert response["code"] == "QIT001007"

        status, response = RequestGenerator.PUT_two_factor("pix-que-nao-existe", "confirmed")
        assert status == 404
        assert response["code"] == "QIT001001"

        status, response = RequestGenerator.PUT_two_factor(transaction_id, "talvez")
        assert status == 400
        assert response["code"] == "QIT000001"

    def test_declined_or_expired_leaves_the_exposure(self):
        """Testes 20' e 20'': o 2FA das 10:00 recusado ou expirado não conta mais nos limites."""
        payer = profile_18g()
        two_factor_id = untrusted_device_two_factor(payer)
        status, _response = RequestGenerator.PUT_two_factor(two_factor_id, "declined", clock=at("2026-10-17 10:05"))
        assert status == 200

        _status, response, transaction_id = payer.pay("2026-10-17 10:30", 150_000, Destination())
        assert response["status"] == "approved"
        assert response["metadata"]["score"] == 0
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 2}

        payer = profile_18g()
        untrusted_device_two_factor(payer)

        _status, response, _transaction_id = payer.pay("2026-10-17 10:30", 150_000, Destination())
        assert response["status"] == "approved"

    def test_the_payer_lock_orders_confirmation_and_analysis(self):
        """Testes 22-A e 22-B: o que vale é o estado no corte de cada análise."""
        # 22-A: NC gravado às 10:04:00, análise recebida às 10:04:01 — enxerga o NC.
        payer = profile_18g()
        two_factor_id = untrusted_device_two_factor(payer)
        status, _response = RequestGenerator.PUT_two_factor(two_factor_id, "declined", clock=at("2026-10-17 10:04:00"))
        assert status == 200

        _status, response, _transaction_id = payer.pay("2026-10-17 10:04", 150_000, Destination(), received="2026-10-17 10:04:01")
        assert response["status"] == "approved"

        # 22-B: a análise pega a trava antes (10:04:01) e o NC só é gravado às 10:04:02.
        payer = profile_18g()
        two_factor_id = untrusted_device_two_factor(payer)

        _status, response, transaction_id = payer.pay("2026-10-17 10:04", 150_000, Destination(), received="2026-10-17 10:04:01")
        assert response["status"] == "two_factor_required"
        assert response["metadata"]["decision_basis"] == "floor_2fa"
        assert ObjectGenerator.fired_floors(transaction_id) == ["F9", "F11"]

        status, _response = RequestGenerator.PUT_two_factor(two_factor_id, "declined", clock=at("2026-10-17 10:04:02"))
        assert status == 200

    def test_confirmed_two_factor_becomes_good_evidence_after_the_confirmation(self):
        """Testes 26b e 26c: F4 até existir um AU — e só para análises recebidas depois dele."""
        payer = Payer(kind="PJ", opened="2023-10-01 10:00")
        supplier = Destination(kind="PJ")
        payer.seed_many(days("2026-08-03 11:00", 30), 100_000, supplier)

        employee = Destination(kind="PF")
        _status, response, first_id = payer.pay("2026-09-07 10:00", 60_000, employee, received="2026-09-07 10:00:00")
        assert response["status"] == "two_factor_required"

        # 26c: recebida às 10:02, antes da confirmação das 10:03 — ainda F4.
        _status, response, transaction_id = payer.pay("2026-09-07 10:02", 60_000, employee, received="2026-09-07 10:02:00")
        assert response["status"] == "two_factor_required"
        assert ObjectGenerator.fired_floors(transaction_id) == ["F4"]

        status, _response = RequestGenerator.PUT_two_factor(first_id, "confirmed", clock=at("2026-09-07 10:03"))
        assert status == 200

        # 26b: 05/10, com o AU de 07/09 — sem F4.
        _status, response, transaction_id = payer.pay("2026-10-05 10:00", 60_000, employee)
        assert response["status"] == "approved"
        assert response["metadata"]["score"] == 0
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 15}
