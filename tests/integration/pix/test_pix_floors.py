from tests.utils import Destination, ObjectGenerator, Payer
from tests.utils.time_utils import days


def assert_decision(response: dict, status: str, score: int, decision_basis: str) -> None:
    assert response["status"] == status, response
    assert response["metadata"]["score"] == score, response
    assert response["metadata"]["decision_basis"] == decision_basis, response


class TestPixFloors:
    """Os pisos de 2FA (seção 5.1), pelos cenários da matriz da seção 12.

    Cada teste monta o histórico do cenário pela API (Payer.seed) e confere
    a decisão e, pela fundamentação do GET, quais pisos dispararam.
    """

    def test_f1_missing_geolocation(self):
        """Testes 24a, 24b e 24c: geolocalização ausente atípica."""
        payer = Payer(opened="2023-10-01 10:00")
        y = Destination()
        payer.seed_many(days("2026-09-20 12:00", 12), 10_000, y)

        # 24a: sem geolocalização, 15.000 para Y — abaixo do limite de F1.
        status, response, transaction_id = payer.pay("2026-10-17 14:00", 15_000, y, geolocation=None)
        assert status == 201
        assert_decision(response, "approved", 0, "score")
        assert ObjectGenerator.attenuators(transaction_id) == {"A1": 10, "A4": 6}

        # 24b: o mesmo cenário com 35.000 — F1.
        payer_b = Payer(opened="2023-10-01 10:00")
        y_b = Destination()
        payer_b.seed_many(days("2026-09-20 12:00", 12), 10_000, y_b)

        status, response, transaction_id = payer_b.pay("2026-10-17 14:00", 35_000, y_b, geolocation=None)
        assert_decision(response, "two_factor_required", 0, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F1"]
        assert ObjectGenerator.attenuators(transaction_id) == {"A4": 6}

    def test_f1_does_not_punish_starting_to_send_geolocation(self):
        """Teste 24c: histórico sem geolocalização, a atual com — nada dispara."""
        payer = Payer(opened="2023-10-01 10:00", geolocation=None)
        y = Destination()
        payer.seed_many(days("2026-09-20 12:00", 12), 10_000, y)

        status, response, transaction_id = payer.pay(
            "2026-10-17 14:00", 35_000, y, geolocation={"latitude": -23.5505, "longitude": -46.6333}
        )
        assert_decision(response, "approved", 0, "score")
        assert ObjectGenerator.fired_floors(transaction_id) == []
        assert ObjectGenerator.attenuators(transaction_id) == {"A4": 6}

    def test_f2_warming_up(self):
        """Testes 2 e 2b: pagador não maduro (4 AP), destino conhecido ou não."""
        def profile():
            payer = Payer(opened="2025-09-12 10:00")
            x = Destination()
            payer.seed_many(["2026-09-19 12:00", "2026-09-26 12:00", "2026-10-03 12:00", "2026-10-10 12:00"], 10_000, x)
            return payer, x

        payer, x = profile()
        status, response, transaction_id = payer.pay("2026-10-17 14:00", 10_000, x)
        assert_decision(response, "approved", 0, "score")
        assert ObjectGenerator.attenuators(transaction_id) == {"A1": 10, "A3": 10, "A4": 2}

        # Um pagador novo com o mesmo perfil: o Pix acima o deixaria maduro.
        payer, x = profile()
        z = Destination(opened="2023-01-01 10:00")
        status, response, transaction_id = payer.pay("2026-10-17 14:00", 60_000, z)
        assert_decision(response, "two_factor_required", 0, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F2"]
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 2}

    def test_f3_and_s12_insisting_after_rejection(self):
        """Testes 11a e 11b: contas PF novas dos dois lados, e a insistência depois da rejeição.

        O RJ das 10:00 nasce de verdade: 100.001 acima do teto de conta nova
        (S1 25) + dispositivo novo (S5 25) + conta de destino de 2 dias (S7
        30) = 80, alto e diverso, rejeição independente.
        """
        payer = Payer(opened="2026-10-12 10:00", device="W")
        mule = Destination(opened="2026-10-15 10:00")

        status, response, _first_id = payer.pay("2026-10-17 10:00", 100_001, mule)
        assert_decision(response, "rejected", 80, "score")

        # 11a: 10:05, 90.000 — F2, F3, S5 25, S7 30 e S12 15 = 70, alto e diverso.
        status, response, transaction_id = payer.pay("2026-10-17 10:05", 90_000, mule)
        assert_decision(response, "rejected", 70, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S5": 25, "S7": 30, "S12": 15}
        consultation = ObjectGenerator.analysis(transaction_id)
        assert set(consultation["reasoning"]["decision"]["fired_floors"]) == {"F2", "F3"}
        assert consultation["independent_rejection"] is False

        # 11b: 10:12 — a RJ das 10:05 não é independente, a das 10:00 saiu da janela.
        status, response, transaction_id = payer.pay("2026-10-17 10:12", 90_000, mule)
        assert_decision(response, "two_factor_required", 55, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S5": 25, "S7": 30}

    def test_f4_pj_paying_new_cpf(self):
        """Teste 26a: o 2FA de 07/09 nunca foi confirmado — F4 se repete."""
        payer = Payer(kind="PJ", opened="2023-10-01 10:00")
        supplier = Destination(kind="PJ")
        payer.seed_many(days("2026-08-03 11:00", 30), 100_000, supplier)

        employee = Destination(kind="PF")
        status, response, _transaction_id = payer.pay("2026-09-07 10:00", 60_000, employee)
        assert_decision(response, "two_factor_required", 0, "floor_2fa")

        status, response, transaction_id = payer.pay("2026-10-05 10:00", 60_000, employee)
        assert_decision(response, "two_factor_required", 0, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F4"]
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 15}

    def test_f5_pj_new_account_high_amount(self):
        payer = Payer(kind="PJ", opened="2026-10-01 10:00")
        supplier = Destination(kind="PJ")

        status, response, transaction_id = payer.pay("2026-10-17 14:00", 500_001, supplier)
        assert response["status"] == "two_factor_required"
        assert "F5" in ObjectGenerator.fired_floors(transaction_id)

    def test_f6_night_limit_boundaries(self):
        """Testes 18a, 18b, 18c e 18f: as fronteiras da noite e do limite de 100.000."""
        cases = [
            ("2026-10-17 19:59:59", 150_000, "approved", 0, "score"),
            ("2026-10-17 20:00:00", 100_000, "approved", 5, "score"),
            ("2026-10-17 20:00:00", 100_001, "two_factor_required", 5, "floor_2fa"),
            ("2026-10-18 06:00:00", 150_000, "approved", 0, "score"),
        ]

        for when, amount, status, score, basis in cases:
            payer, _y = ObjectGenerator.m30_payer(amount=500_000)
            status_code, response, _transaction_id = payer.pay(when, amount, Destination())
            assert status_code == 201
            assert_decision(response, status, score, basis)

    def test_f6_night_crosses_midnight_and_sees_later_events(self):
        """Testes 18d e 18e: a noite inteira conta, inclusive o que está datado depois de t."""
        payer, _y = ObjectGenerator.m30_payer(amount=500_000)
        payer.seed("2026-10-17 23:50", 70_000, Destination())

        status, response, transaction_id = payer.pay("2026-10-18 00:10", 40_000, Destination())
        assert_decision(response, "two_factor_required", 5, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F6"]

        late_payer, _y = ObjectGenerator.m30_payer(amount=500_000)
        late_payer.seed("2026-10-17 22:40", 70_000, Destination())

        status, response, transaction_id = late_payer.pay(
            "2026-10-17 22:30", 40_000, Destination(), received="2026-10-17 22:41"
        )
        assert_decision(response, "two_factor_required", 5, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F6"]

    def test_f6_sums_every_source_account_of_the_payer(self):
        """Teste 29c: conta A às 22:00 e conta B (mesmo CPF) às 22:01 — 160.000 na noite."""
        payer, _y = ObjectGenerator.m30_payer()
        nz = Destination()
        payer.seed("2026-10-17 22:00", 80_000, nz, account="11111111")

        status, response, transaction_id = payer.pay("2026-10-17 22:01", 80_000, nz, account="22222222")
        assert_decision(response, "two_factor_required", 18, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F6"]
        assert ObjectGenerator.fired_rules(transaction_id) == {"S3": 25, "S11": 10}

    def test_f7_night_pj_with_maturity(self):
        """Testes 19 e 19': L = max(1.000.000; min(5.000.000; 2 × 210.000)) = 1.000.000."""
        nightly_totals = [150_000, 200_000, 220_000, 250_000, 300_000, 180_000]
        nights = ["2026-08-19", "2026-08-26", "2026-09-02", "2026-09-09", "2026-09-16", "2026-09-23"]

        def build_payer():
            payer = Payer(kind="PJ", opened="2023-10-01 10:00")
            supplier = Destination(kind="PJ")

            moments = days("2026-08-03 11:00", 30)
            for night, total in zip(nights, nightly_totals):
                moments.append(f"{night} 21:00")
                moments.append(f"{night} 22:00")

            moments.sort()
            amounts = {}
            for night, total in zip(nights, nightly_totals):
                amounts[f"{night} 21:00"] = total // 2
                amounts[f"{night} 22:00"] = total - total // 2

            for when in moments:
                payer.seed(when, amounts.get(when, 100_000), supplier)

            return payer, supplier

        payer, supplier = build_payer()
        status, response, transaction_id = payer.pay("2026-10-17 22:00", 900_000, supplier)
        assert_decision(response, "approved", 13, "score")
        assert ObjectGenerator.fired_rules(transaction_id) == {"S3": 25}

        f7 = [floor for floor in ObjectGenerator.analysis(transaction_id)["reasoning"]["floors"] if floor["code"] == "F7"][0]
        assert f7["R"] == 210_000
        assert f7["threshold"] == 1_000_000

        payer, supplier = build_payer()
        status, response, transaction_id = payer.pay("2026-10-17 22:00", 1_000_001, supplier)
        assert_decision(response, "two_factor_required", 18, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F7"]
        assert ObjectGenerator.fired_rules(transaction_id) == {"S3": 35}

    def test_f8_untrusted_device(self):
        """Testes 7 e 7b: M30 com um AP de 5.000 no dispositivo X na véspera."""
        payer, _y = ObjectGenerator.m30_payer()
        payer.seed("2026-10-16 12:00", 5_000, Destination(), device="X")

        status, response, transaction_id = payer.pay("2026-10-17 16:00", 30_000, Destination(), device="X")
        assert_decision(response, "approved", 8, "score")

        status, response, transaction_id = payer.pay("2026-10-17 16:00:30", 150_000, Destination(), device="X")
        assert_decision(response, "two_factor_required", 35, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F8"]
        assert ObjectGenerator.fired_rules(transaction_id) == {"S3": 35, "S5": 15}

    def test_f8_new_device_boundaries(self):
        """Testes 23b, 23b', 23c e 23c': M30 alto num dispositivo novo."""
        cases = [
            (20_000, "approved", 0, "score"),
            (20_001, "approved", 13, "score"),
            (100_000, "approved", 13, "score"),
            (100_001, "two_factor_required", 13, "floor_2fa"),
        ]

        for amount, status, score, basis in cases:
            payer, _y = ObjectGenerator.m30_payer(amount=500_000)
            _status, response, _transaction_id = payer.pay("2026-10-17 14:00", amount, Destination(), device="D7")
            assert_decision(response, status, score, basis)

    def test_f9_ceiling_in_60_minutes(self):
        """Testes 23a e 23a': M30 alto, teto 2.000.000 — o igual passa, um centavo acima não."""
        payer, y = ObjectGenerator.m30_payer(amount=500_000)
        _status, response, _transaction_id = payer.pay("2026-10-17 14:00", 2_000_000, Destination())
        assert_decision(response, "approved", 0, "score")

        payer, y = ObjectGenerator.m30_payer(amount=500_000)
        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 2_000_001, Destination())
        assert_decision(response, "two_factor_required", 13, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F9"]
        assert ObjectGenerator.fired_rules(transaction_id) == {"S1": 25}

    def test_f9_fractioning_and_late_events(self):
        """Testes 9, 18g, 18g' e 18h: a janela de 60 min, a fronteira dela e o evento atrasado."""

        def profile_18g():
            payer = Payer(opened="2026-09-27 10:00", device="D2")
            y = Destination()
            payer.seed_many(days("2026-10-01 12:00", 5), 100_000, y)
            return payer, y

        # 18g: AP às 14:00:00 fica FORA de (14:00:00, 15:00:00] — só F11.
        payer, y = profile_18g()
        payer.seed("2026-10-17 14:00:00", 200_000, Destination())
        _status, response, transaction_id = payer.pay("2026-10-17 15:00:00", 150_000, Destination())
        assert_decision(response, "two_factor_required", 0, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F11"]
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 2}

        # 18g': às 14:00:01 entra na janela — F9 e F11.
        payer, y = profile_18g()
        payer.seed("2026-10-17 14:00:01", 200_000, Destination())
        _status, response, transaction_id = payer.pay("2026-10-17 15:00:00", 150_000, Destination())
        assert_decision(response, "two_factor_required", 0, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F9", "F11"]

        # 18h: o Pix datado de 14:30 foi recebido antes; o atual é datado de 14:20.
        payer, y = profile_18g()
        payer.seed("2026-10-17 14:30", 200_000, Destination())
        _status, response, transaction_id = payer.pay("2026-10-17 14:20", 150_000, Destination(), received="2026-10-17 14:31")
        assert_decision(response, "two_factor_required", 0, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F9", "F11"]

    def test_f9_fractioned_payments_with_s3(self):
        """Teste 9: quatro Pix fracionados em 14:10–14:52 e o quinto às 15:00."""
        payer = Payer(opened="2026-09-27 10:00", device="D2")
        y = Destination()
        payer.seed_many(days("2026-10-01 12:00", 5), 10_000, y)

        for when, amount in [("14:10", 64_000), ("14:25", 91_000), ("14:38", 45_500), ("14:52", 112_000)]:
            payer.seed(f"2026-10-17 {when}", amount, Destination())

        _status, response, transaction_id = payer.pay("2026-10-17 15:00", 78_000, Destination())
        assert_decision(response, "two_factor_required", 13, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F9", "F11"]
        assert ObjectGenerator.fired_rules(transaction_id) == {"S3": 25}
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 2}

    def test_f10_test_then_high_amount(self):
        """Teste 13: M30; Pix de 100 para o amigo (conta de 12 dias) e, 6 min depois, 150.000."""
        payer, _y = ObjectGenerator.m30_payer()
        friend = Destination(opened="2026-10-05 10:00")
        payer.seed("2026-10-17 10:00", 100, friend)

        _status, response, transaction_id = payer.pay("2026-10-17 10:06", 150_000, friend)
        assert_decision(response, "two_factor_required", 25, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F10"]
        assert ObjectGenerator.fired_rules(transaction_id) == {"S3": 35, "S7": 15}
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 15}

    def test_f10_several_small_tests_do_not_hide_each_other(self):
        """Teste 29a: Pix de 5, 10, 2 e 20 para NB (conta de 5 dias) e 100.000 às 14:40."""
        payer, _y = ObjectGenerator.m30_payer()
        nb = Destination(opened="2026-10-12 10:00")

        for when, amount in [("13:50", 5), ("14:05", 10), ("14:20", 2), ("14:35", 20)]:
            payer.seed(f"2026-10-17 {when}", amount, nb)

        _status, response, transaction_id = payer.pay("2026-10-17 14:40", 100_000, nb)
        assert_decision(response, "two_factor_required", 30, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F10"]
        assert ObjectGenerator.fired_rules(transaction_id) == {"S3": 25, "S7": 30}

    def test_f11_onboarding_ramp_pf(self):
        """Testes 28a e 28b: PF nova, nível 0 (100.000 por dia)."""
        payer = Payer(opened="2024-10-17 10:00")
        _status, response, _transaction_id = payer.pay("2026-10-17 14:00", 50_000, Destination())
        assert_decision(response, "approved", 25, "score")

        payer = Payer(opened="2024-10-17 10:00")
        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 100_001, Destination())
        assert_decision(response, "two_factor_required", 25, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F2", "F8", "F11"]

    def test_f11_good_days_raise_the_level(self):
        """Testes 28c e 28d: três dias bons — nível 2, 500.000 por dia."""
        for amount, status, basis in [(500_000, "approved", "score"), (500_001, "two_factor_required", "floor_2fa")]:
            payer = Payer(opened="2024-10-17 10:00")
            y = Destination()
            payer.seed_many(days("2026-10-08 12:00", 3), 50_000, y)

            _status, response, transaction_id = payer.pay("2026-10-17 14:00", amount, y)
            assert_decision(response, status, 0, basis)
            assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 1}

    def test_f11_independent_rejection_lowers_the_level(self):
        """Teste 28e: uma rejeição independente em 16/10 23:00 — nível 2 − 1 = 1 (200.000)."""
        payer = Payer(opened="2024-10-17 10:00")
        y = Destination()
        payer.seed_many(days("2026-10-08 12:00", 3), 50_000, y)

        # A rejeição independente: acima do teto (S1), dispositivo novo (S5),
        # conta de destino de 0 dias (S7) — 80 − A4 1 = 79, alto e diverso.
        status, rejected, _rejected_id = payer.pay(
            "2026-10-16 23:00", 2_000_001, Destination(opened="2026-10-16 22:00"), device="D9"
        )
        assert rejected["status"] == "rejected"

        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 300_000, y)
        assert_decision(response, "two_factor_required", 0, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F11"]
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10}

    def test_f11_too_many_requests_drops_to_level_zero(self):
        """Teste 28f: o 11º pedido do dia derruba a rampa para o nível 0 (100.000)."""
        payer = Payer(opened="2024-10-17 10:00")
        y = Destination()
        payer.seed_many(days("2026-10-08 12:00", 3), 50_000, y)

        for minutes in range(0, 300, 30):
            hour = 8 + minutes // 60
            payer.seed(f"2026-10-17 {hour:02d}:{minutes % 60:02d}", 1_000, Destination())

        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 150_000, y)
        assert_decision(response, "two_factor_required", 24, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F11"]
        assert ObjectGenerator.fired_rules(transaction_id) == {"S3": 35}

    def test_f11_pj_and_outside_the_ramp(self):
        """Testes 28g (PJ nova, 500.000 por dia) e 28h (primeiro Pix há 31 dias: fora da rampa)."""
        payer = Payer(kind="PJ", opened="2024-10-17 10:00")
        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 600_000, Destination(kind="PJ"))
        assert_decision(response, "two_factor_required", 25, "floor_2fa")
        assert ObjectGenerator.fired_floors(transaction_id) == ["F11"]

        payer = Payer(opened="2024-10-17 10:00")
        y = Destination()
        payer.seed_many(days("2026-09-16 12:00", 3), 50_000, y)
        _status, response, transaction_id = payer.pay("2026-10-17 14:00", 150_000, y)
        assert_decision(response, "approved", 0, "score")
        assert ObjectGenerator.attenuators(transaction_id) == {"A3": 10, "A4": 1}
