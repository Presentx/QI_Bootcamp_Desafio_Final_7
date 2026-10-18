from tests.utils import Destination, ObjectGenerator, Payer
from tests.utils.payload_generator import RECIFE, RIO_DE_JANEIRO, SAO_PAULO
from tests.utils.time_utils import days


def assert_decision(response: dict, status: str, score: int, decision_basis: str) -> None:
    assert response["status"] == status, response
    assert response["metadata"]["score"] == score, response
    assert response["metadata"]["decision_basis"] == decision_basis, response


class TestPixScore:
    """As regras de score, os atenuantes e a fórmula (seções 5.2, 5.3 e 6)."""

    def test_new_device_first_payment(self):
        """Teste 1: PF com conta de 400 dias, nenhum evento, D9, 30.000 — S5 +25, approved."""
        payer = Payer(opened="2025-09-12 10:00", device="D9")
        z = Destination(opened="2024-10-17 10:00")

        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 30_000, z)
        assert_decision(response, "approved", 25, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S5": 25}

    def test_new_pj_destination_account(self):
        """Teste 3: M30 pagando 8.000 a uma loja com conta aberta há 3 dias — S7 +25, D* 12."""
        payer, _y = ObjectGenerator.m30_payer()
        store = Destination(kind="PJ", opened="2026-10-14 10:00")

        _status, response, transaction_id = payer.pay("2026-10-17 15:00", 8_000, store)
        assert_decision(response, "approved", 13, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S7": 25}
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 15}

    def test_a1_suppresses_s3_for_the_rent(self):
        """Teste 4: aluguel de 250.000 todo dia 5 — A1 suprime S3 e o score zera."""
        payer = Payer()
        y = Destination()
        landlord = Destination()

        schedule = []
        for month in range(4, 10):
            schedule.append((f"2026-{month:02d}-05 10:00", 250_000, landlord))
        for day in range(1, 31):
            schedule.append((f"2026-08-{day:02d} 12:00", 10_000, y))
        schedule.sort(key=lambda item: item[0])

        for when, amount, destination in schedule:
            payer.seed(when, amount, destination)

        _status, response, transaction_id = payer.pay("2026-10-05 10:00", 250_000, landlord)
        assert_decision(response, "approved", 0, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {}
        assert ObjectGenerator.attenuators(transaction_id) == {"A1": 10, "A3": 10, "A4": 15}

        reasoning = ObjectGenerator.analysis(transaction_id)["reasoning"]
        assert reasoning["suppressions"] == [{"rule": "S3", "by": "A1"}]

    def test_a1_with_three_old_payments(self):
        """Teste 5: só 3 Pix de 60.000 na vida, o último em fevereiro — A1, sem A3 nem A4."""
        payer = Payer(opened="2023-10-17 10:00")
        x = Destination()
        payer.seed_many(["2025-11-10 12:00", "2025-12-10 12:00", "2026-02-10 12:00"], 60_000, x)

        _status, response, transaction_id = payer.pay("2026-10-17 12:00", 70_000, x)
        assert_decision(response, "approved", 0, "score")
        assert ObjectGenerator.attenuators(transaction_id) == {"A1": 10}

    def test_pj_with_two_habitual_locations(self):
        """Teste 6: PJ alternando dispositivo A em SP e B em Recife — sábado, B em Recife, aprovado."""
        payer = Payer(kind="PJ")
        supplier = Destination(kind="PJ")

        for index, when in enumerate(days("2026-08-01 11:00", 40)):
            if index % 2 == 0:
                payer.seed(when, 400_000, supplier, device="A", geolocation=SAO_PAULO)
            else:
                payer.seed(when, 400_000, supplier, device="B", geolocation=RECIFE)

        payer.seed("2026-10-17 10:00", 400_000, supplier, device="A", geolocation=SAO_PAULO)

        _status, response, transaction_id = payer.pay("2026-10-17 10:10", 400_000, supplier, device="B", geolocation=RECIFE)
        assert_decision(response, "approved", 0, "score")
        assert ObjectGenerator.attenuators(transaction_id) == {"A1": 10, "A3": 10, "A4": 15}

    def test_s2_and_the_value_group_cap(self):
        """S2: 5.000.000 em 24h acima de 2 × 2.000.000; Valor = min(20 + 35; 40) = 40."""
        payer, _y = ObjectGenerator.m30_payer()
        payer.seed("2026-10-17 08:00", 2_000_000, Destination())
        payer.seed("2026-10-17 10:00", 2_000_000, Destination())

        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 1_000_000, Destination())
        assert_decision(response, "approved", 20, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S2": 20, "S3": 35}

        score = ObjectGenerator.analysis(transaction_id)["reasoning"]["score"]
        assert score["points_after_cap"]["valor"] == 40
        assert score["P"] == 40
        assert score["D_star"] == 20

    def test_s4_pj_to_pf_after_hours_suppresses_s11(self):
        """S4: PJ paga 600.000 a um CPF num sábado, sem referências PJ→PF; S11 suprimida por S4."""
        payer = Payer(kind="PJ")
        supplier = Destination(kind="PJ")
        weekdays = [3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 17, 18]
        payer.seed_many([f"2026-08-{day:02d} 11:00" for day in weekdays], 100_000, supplier)

        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 600_000, Destination(kind="PF"))
        assert_decision(response, "two_factor_required", 13, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F4"]
        assert ObjectGenerator.fired_rules(transaction_id) == {"S4": 25}

        reasoning = ObjectGenerator.analysis(transaction_id)["reasoning"]
        assert reasoning["suppressions"] == [{"rule": "S11", "by": "S4"}]

    def test_warming_up_with_cents_is_rejected(self):
        """Teste 12: 30 Pix de 500 em dois dias, depois 250.000 para conta de 3 dias — rejected.

        S3 +35 (mediana 500), S5 +15 (X observado), S7 +30: 80, alto, três
        grupos — e continua rejected sem S12 e com A4 cheio: independente.
        """
        payer = Payer(opened="2026-10-07 10:00", device="X")
        y = Destination()

        moments = []
        for day in ("15", "16"):
            for minute in range(15):
                moments.append(f"2026-10-{day} 10:{minute:02d}")
        payer.seed_many(moments, 500, y)

        mule = Destination(opened="2026-10-14 10:00")
        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 250_000, mule)
        assert_decision(response, "rejected", 80, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S3": 35, "S5": 15, "S7": 30}

        consultation = ObjectGenerator.analysis(transaction_id)
        assert consultation["independent_rejection"] is True
        assert set(consultation["reasoning"]["decision"]["fired_floors"]) == {"F2", "F3", "F8", "F11"}

    def test_s8_new_payers_to_a_mule_account(self):
        """Testes 27a e 27c: conta PF de 10 dias recebendo de pagadores novos."""
        mule = Destination(opened="2026-10-07 10:00")

        for minute in ("09:00", "09:01", "09:02"):
            Payer().seed(f"2026-10-17 {minute}", 5_000, mule)

        u8, _y8 = ObjectGenerator.m30_payer()
        u9, _y9 = ObjectGenerator.m30_payer()

        _status, response, transaction_id = u8.pay("2026-10-17 10:00", 10_000, mule, received="2026-10-17 10:00:00")
        assert_decision(response, "approved", 8, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S7": 15}

        _status, response, transaction_id = u9.pay("2026-10-17 10:00", 10_000, mule, received="2026-10-17 10:00:01")
        assert_decision(response, "approved", 18, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S7": 15, "S8": 20}

    def test_s9_outside_habitual_locations(self):
        payer, _y = ObjectGenerator.m30_payer()

        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 10_000, Destination(), geolocation=RECIFE)
        assert_decision(response, "approved", 8, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S9": 15}
        assert ObjectGenerator.attenuators(transaction_id) == {"A4": 15}

    def test_s10_impossible_travel(self):
        """Testes 30a, 30b e 30c: SP às 14:00:00.000 e o Rio logo depois."""
        payer = Payer(opened="2025-09-12 10:00")
        payer.seed("2026-10-17 14:00:00.000", 10_000, Destination())
        _status, response, transaction_id = payer.pay("2026-10-17 14:00:00.000", 10_000, Destination(), geolocation=RIO_DE_JANEIRO)
        assert_decision(response, "approved", 0, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {}

        payer = Payer(opened="2025-09-12 10:00")
        payer.seed("2026-10-17 14:00:00.000", 10_000, Destination())
        _status, response, transaction_id = payer.pay("2026-10-17 14:00:00.001", 10_000, Destination(), geolocation=RIO_DE_JANEIRO)
        assert_decision(response, "approved", 25, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S10": 25}

        # 30c: o Pix de SP às 17:00Z e o do Rio às 14:30−03:00 (17:30Z): 720 km/h.
        payer = Payer(opened="2025-09-12 10:00")
        payer.seed("2026-10-17 14:00", 10_000, Destination())
        _status, response, transaction_id = payer.pay("2026-10-17 14:30", 10_000, Destination(), geolocation=RIO_DE_JANEIRO)
        assert_decision(response, "approved", 0, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {}

    def test_s3_keeps_small_payments_in_the_median(self):
        """Teste 29b: centavos para a conta NC antes de 200.000 — F2 e S3 +35."""
        payer = Payer(opened="2026-09-27 10:00")
        payer.seed_many(days("2026-10-01 12:00", 3), 15_000, Destination())

        nc = Destination(opened="2023-01-01 10:00")
        for when, cents in [("13:00", 1), ("13:15", 2), ("13:30", 3), ("13:45", 5)]:
            payer.seed(f"2026-10-17 {when}", cents, nc)

        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 200_000, nc)
        assert_decision(response, "two_factor_required", 24, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F2"]
        assert ObjectGenerator.fired_rules(transaction_id) == {"S3": 35}
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 1}

    def test_s3_cost_for_many_small_legitimate_payments(self):
        """Teste 29d: 16 Pix de 500 na véspera puxam a mediana para 500 — S3 +35, ainda aprovado."""
        payer = Payer()
        y = Destination()
        payer.seed_many(days("2026-08-01 12:00", 14, step_days=3), 100_000, y)
        payer.seed_many([f"2026-10-16 {18 + minute // 60}:{minute % 60:02d}" for minute in range(0, 80, 5)], 500, y)

        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 60_000, Destination())
        assert_decision(response, "approved", 18, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S3": 35}
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 7}

    def test_a2_same_owner(self):
        payer = Payer(opened="2025-09-12 10:00", device="D9")
        own_account = Destination(document=payer.document, opened="2024-01-01 10:00")

        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 30_000, own_account)
        assert_decision(response, "approved", 15, "score")
        assert ObjectGenerator.attenuators(transaction_id) == {"A2": 10}
