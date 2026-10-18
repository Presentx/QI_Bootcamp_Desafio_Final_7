"""Casos de borda e brechas do Motor Antifraude Pix (Política v6).

Testes de caixa-preta: falam com a API só por HTTP e nunca importam nada
de src/. O banco é lido só para conferir, no caso 9, que o payload
original continua gravado.

Histórico antigo (pagador maduro, celular confiável, fora da rampa F11)
entra pela própria API com o cabeçalho X-Test-Clock, ligado com APP_ENV
local ou test: cada Pix do histórico é "recebido" na sua data. Quando a
análise pede 2FA, o teste confirma pela rota E1 e o Pix vira evidência.

A resposta só traz {key, status, metadata}. Quando um caso quer provar que
UM piso disparou, ele monta o cenário para que nenhum outro piso possa
disparar e o score fique baixo; aí two_factor_required com floor_2fa só
pode vir do piso em teste.

Valores em centavos. Horário civil America/Sao_Paulo. Os cenários foram
conferidos no simulador de referência da política em seis horários do dia:
o resultado não depende da hora em que a suíte roda.
"""
import random
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from os import environ
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, text

from tests.utils import INTERNAL_TOKEN, DbUtils
from tests.utils.requisition import ClientRequisition

SP_TZ = ZoneInfo("America/Sao_Paulo")
SAO_PAULO = (-23.560000, -46.650000)
RIO = (-22.900000, -43.170000)
DEST_ISPB = "60701190"
# ISPB da instituição que opera o motor (o contrato não traz o participante da
# conta de origem). Precisa ser o mesmo valor que a aplicação usa ao gravar a
# conta de origem; só o caso F6 depende dele, e lá as duas contas vêm pela API.
SOURCE_ISPB = environ.get("SOURCE_ISPB", "00000000")
POLICY_VERSION = "6.0.0"
STATUSES = {"approved", "two_factor_required", "rejected"}
HEADERS = {"INTERNAL-TOKEN": INTERNAL_TOKEN}


# ---------------------------------------------------------------- documentos

def _dv(digits, weights):
    total = sum(int(d) * w for d, w in zip(digits, weights))
    rest = total % 11
    return "0" if rest < 2 else str(11 - rest)


def new_cpf() -> str:
    while True:
        base = "".join(str(random.randint(0, 9)) for _ in range(9))
        if len(set(base)) > 1:
            break
    d1 = _dv(base, range(10, 1, -1))
    d2 = _dv(base + d1, range(11, 1, -1))
    return base + d1 + d2


def new_cnpj() -> str:
    base = "".join(str(random.randint(0, 9)) for _ in range(8)) + "0001"
    d1 = _dv(base, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = _dv(base + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base + d1 + d2


def new_account_number() -> str:
    return str(random.randint(10000, 99999999))


# ---------------------------------------------------------------- tempo

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime, millis: bool = False) -> str:
    return moment.astimezone(SP_TZ).isoformat(timespec="milliseconds" if millis else "seconds")


def night_instant(reference: datetime) -> datetime:
    """Um instante na noite civil de São Paulo [20:00, 06:00), admissível agora.

    Se a referência menos 5 min já está na noite, usa esse instante. Senão,
    usa as 22:00 de ontem: de 06:00 às 20:00 isso fica entre 8h e 22h atrás,
    sempre dentro da janela de 24h da admissibilidade.
    """
    candidate = (reference - timedelta(minutes=5)).astimezone(SP_TZ).replace(microsecond=0)
    if candidate.hour >= 20 or candidate.hour < 6:
        return candidate
    return (candidate - timedelta(days=1)).replace(hour=22, minute=0, second=0)


# ---------------------------------------------------------------- partes

class Party:
    """Uma pessoa com uma conta e, se for pagador, um celular."""

    def __init__(self, document=None, opening_date=None, branch="0001", account_number=None,
                 participant=DEST_ISPB, device_id=None):
        self.document = document or new_cpf()
        self.branch = branch
        self.account_number = account_number or new_account_number()
        self.participant = participant
        self.opening_date = opening_date or (now_utc() - timedelta(days=3 * 365))
        self.device_id = device_id or f"dvc-{uuid.uuid4().hex[:12]}"

    @property
    def person_type(self) -> str:
        return "PF" if len(self.document) == 11 else "PJ"


def build_payload(pix_id, payer: Party, receiver: Party, amount: int, moment: datetime,
                  geo=SAO_PAULO, millis=False, source_branch=None, source_account=None) -> dict:
    payload = {
        "id": pix_id,
        "amount": amount,
        "transaction_date": iso(moment, millis),
        "source_account": {
            "branch": source_branch or payer.branch,
            "account_number": source_account or payer.account_number,
            "opening_date": iso(payer.opening_date),
            "owner": {"document_number": payer.document},
        },
        "destination_account": {
            "participant": receiver.participant,
            "branch": receiver.branch,
            "account_number": receiver.account_number,
            "opening_date": iso(receiver.opening_date),
            "owner": {"document_number": receiver.document},
        },
        "source": {
            "device_id": payer.device_id,
            "channel": "app",
            "ip": "198.51.100.23",
        },
    }
    if geo is not None:
        payload["source"]["geolocation"] = {"latitude": geo[0], "longitude": geo[1]}
    return payload


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------- HTTP

def post(payload):
    return ClientRequisition.send("POST", "/pix/transaction", payload=payload, headers=HEADERS)


def get(pix_id):
    return ClientRequisition.send("GET", f"/pix/transaction/{pix_id}", headers=HEADERS)


def assert_not_500(response):
    assert response.response_status < 500 or response.response_status == 503, (
        f"{response.method} {response.endpoint} quebrou: {response.response_status} {response.response_json}"
    )


def assert_decision(response, expected_status, expected_basis=None, http=201):
    assert_not_500(response)
    assert response.response_status == http, response.response_json
    body = response.response_json
    assert body["status"] == expected_status, body
    if expected_basis is not None:
        assert body["metadata"]["decision_basis"] == expected_basis, body
    return body


# ---------------------------------------------------------------- semeador

class HistorySeeder:
    """Monta histórico antigo pela própria API, com o relógio de teste.

    Cada Pix entra por POST com o cabeçalho X-Test-Clock = instante + 1 s
    (APP_ENV local/test). Se a análise pedir 2FA, o teste confirma pela
    rota E1 um minuto depois, e o Pix vira evidência boa (AU).
    """

    def __init__(self):
        self.engine = create_engine(DbUtils.database_url())

    def close(self):
        self.engine.dispose()

    def seed_approved(self, payer: Party, receiver: Party, amount: int, moments, geo=SAO_PAULO):
        for moment in moments:
            pix_id = new_id("seed")
            clock = {"X-Test-Clock": iso(moment + timedelta(seconds=1), millis=True)}
            response = ClientRequisition.send("POST", "/pix/transaction", payload=build_payload(pix_id, payer, receiver, amount, moment, geo=geo),
                                              headers={**HEADERS, **clock})
            assert response.response_status == 201, response.response_json
            status = response.response_json["status"]
            if status == "two_factor_required":
                confirm = ClientRequisition.send("PUT", f"/pix/transaction/{pix_id}/two_factor", payload={"result": "confirmed"},
                                                 headers={**HEADERS, "X-Test-Clock": iso(moment + timedelta(minutes=1), millis=True)})
                assert confirm.response_status == 200, confirm.response_json
            else:
                assert status == "approved", response.response_json

    def stored_transaction(self, pix_id: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT amount, destination_account AS account_number FROM pix_transaction WHERE transaction_id = :i"
            ), {"i": pix_id}).mappings().one()
            return dict(row)

    def count_payer_transactions(self, document: str) -> int:
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT count(*) FROM pix_transaction WHERE payer_document = :d"), {"d": document}).scalar_one()


def noon_days_ago(days: int) -> datetime:
    return (now_utc() - timedelta(days=days)).astimezone(SP_TZ).replace(hour=12, minute=0, second=0, microsecond=0)


def mature_payer(seeder: HistorySeeder, known: Party, count=10, amount=100_000, start_days=40, step=3) -> Party:
    """Pagador PF fora da rampa F11, maduro (F2), com celular confiável e local habitual em SP.

    Padrão: 10 Pix aprovados de R$ 1.000 para `known`, às 12:00, de 40 a 13 dias
    atrás, a cada 3 dias. Mediana de S3 = R$ 1.000; A4 = −5; A3 = −10; A1 vale
    para `known` até R$ 1.500.
    """
    payer = Party(opening_date=now_utc() - timedelta(days=3 * 365))
    moments = [noon_days_ago(start_days - i * step) for i in range(count)]
    seeder.seed_approved(payer, known, amount, moments)
    return payer


@pytest.fixture(scope="module", autouse=True)
def clean_database():
    DbUtils.rollback()
    yield


@pytest.fixture
def seeder():
    s = HistorySeeder()
    yield s
    s.close()


# =============================================================== 1

class TestGetContract:
    def test_get_returns_the_frozen_contract(self):
        """Caso 1. O POST devolve exatamente {key, status, metadata}; o GET devolve
        essa mesma resposta congelada e, ao lado, a análise gravada.

        Esperado (v6, seção 7): 201 no POST com só as três chaves e metadata
        sem detalhe de regra; 200 no GET com key, status e metadata idênticos.
        Quebra: POST com chave extra, ou GET com resposta diferente da do POST.
        """
        payer, receiver = Party(), Party()
        pix_id = new_id("pix-get")
        created = post(build_payload(pix_id, payer, receiver, 10_000, now_utc()))
        assert_not_500(created)
        assert created.response_status == 201, created.response_json
        body = created.response_json

        fetched = get(pix_id)
        assert fetched.response_status == 200, fetched.response_json
        frozen = {k: fetched.response_json[k] for k in ("key", "status", "metadata")}
        assert frozen == body
        assert set(body) == {"key", "status", "metadata"}, body
        assert set(body["metadata"]) == {"score", "score_band", "decision_basis", "policy_version"}
        assert body["status"] in STATUSES


# =============================================================== 2

class TestIdNormalization:
    def test_id_with_outer_spaces_is_trimmed(self):
        """Caso 2A. 68 caracteres brutos ("  " + 64 + "  "), 64 depois do strip.

        Esperado (v6, seção 2): o limite de 64 vale depois de remover os
        espaços das pontas: 201, e o GET pelo id limpo encontra a análise.
        Quebra: 400 (schema aplicou maxLength no valor bruto) ou 404 no GET
        (gravou o id com os espaços).
        """
        clean_id = uuid.uuid4().hex + uuid.uuid4().hex  # 64 caracteres [0-9a-f]
        raw_id = "  " + clean_id + "  "
        assert len(raw_id) == 68 and len(clean_id) == 64

        created = post(build_payload(raw_id, Party(), Party(), 10_000, now_utc()))
        assert_not_500(created)
        assert created.response_status == 201, created.response_json

        fetched = get(clean_id)
        assert fetched.response_status == 200, fetched.response_json
        assert fetched.response_json["key"] == created.response_json["key"]

    def test_id_made_only_of_spaces_is_400(self):
        """Caso 2B. Esperado: 400 QIT000001. Quebra: 201, 422 ou 500."""
        response = post(build_payload("   ", Party(), Party(), 10_000, now_utc()))
        assert_not_500(response)
        assert response.response_status == 400, response.response_json
        assert response.response_json["code"] == "QIT000001"


# =============================================================== 3

class TestImpossibleDates:
    @pytest.mark.parametrize("field, value", [
        ("transaction_date", "2026-02-30T14:00:00-03:00"),
        ("source_opening_date", "2025-13-45T10:00:00-03:00"),
        ("destination_opening_date", "2026-04-31T09:00:00-03:00"),
    ])
    def test_calendar_invalid_date_is_400(self, field, value):
        """Caso 3. Formato ISO que passa num regex, mas não existe no calendário.

        Esperado (v6, seção 2, etapa estrutural): 400 QIT000001.
        Quebra: 500 QIT000500 (ValueError do parse escapou) ou 422.
        """
        payload = build_payload(new_id("pix-date"), Party(), Party(), 10_000, now_utc())
        if field == "transaction_date":
            payload["transaction_date"] = value
        elif field == "source_opening_date":
            payload["source_account"]["opening_date"] = value
        else:
            payload["destination_account"]["opening_date"] = value

        response = post(payload)
        assert_not_500(response)
        assert response.response_status == 400, response.response_json
        assert response.response_json["code"] == "QIT000001"


# =============================================================== 4

class TestImpossibleTravelDivision:
    @pytest.mark.parametrize("gap", [timedelta(0), timedelta(milliseconds=1)])
    def test_same_instant_sp_then_rio_does_not_crash(self, gap):
        """Caso 4. S10 com Δt = 0 ou 1 ms entre São Paulo e Rio (~360 km).

        Esperado (v6, S10 e seção 3.3): Δt é calculado em UTC. Com Δt = 0 a
        regra não roda (o evento de SP nem é "anterior", e a política proíbe
        dividir por zero); com 1 ms a velocidade é enorme e S10 soma +25.
        Nos dois casos: 201 e status válido.
        Quebra: 500 por ZeroDivisionError ou por velocidade infinita.
        """
        payer, receiver = Party(), Party()
        t = now_utc().replace(microsecond=0)
        first = post(build_payload(new_id("pix-s10a"), payer, receiver, 10_000, t, geo=SAO_PAULO, millis=True))
        assert_not_500(first)
        assert first.response_status == 201, first.response_json

        second = post(build_payload(new_id("pix-s10b"), payer, receiver, 10_000, t + gap, geo=RIO, millis=True))
        assert_not_500(second)
        assert second.response_status == 201, second.response_json
        assert second.response_json["status"] in STATUSES


# =============================================================== 5

class TestF10TestThenBigAmount:
    def _run(self, seeder, test_offsets):
        known = Party(opening_date=now_utc() - timedelta(days=5 * 365))
        payer = mature_payer(seeder, known)
        # Destino PJ com conta de 5 dias: S7 +25; F6 não se aplica (só PF→PF),
        # então o resultado não depende de o teste rodar à noite.
        new_account = Party(document=new_cnpj(), opening_date=now_utc() - timedelta(days=5))
        t = now_utc()
        for offset, amount in zip(test_offsets, (100, 200)):
            probe = post(build_payload(new_id("pix-f10-probe"), payer, new_account, amount, t - offset))
            assert_decision(probe, "approved")
        return post(build_payload(new_id("pix-f10-big"), payer, new_account, 200_000, t))

    def test_two_probes_inside_window_still_trigger_f10(self, seeder):
        """Caso 5A. Testes de R$ 1 (t − 10 min) e R$ 2 (t − 5 min), depois R$ 2.000.

        Esperado (v6, F10): o teste de t − 10 min não tem evidência boa antes
        dele, então F10 dispara: two_factor_required com floor_2fa (score 13,
        baixo; nenhum outro piso pode disparar neste pagador).
        Quebra: approved (o segundo teste mascarou o primeiro).
        """
        response = self._run(seeder, (timedelta(minutes=10), timedelta(minutes=5)))
        assert_decision(response, "two_factor_required", "floor_2fa")

    def test_probe_outside_window_does_not_mask_the_next_one(self, seeder):
        """Caso 5B. Testes em t − 40 min e t − 20 min (brecha fechada na v5).

        O teste de t − 40 está fora da janela de 30 min. Na v4 ele contava
        como relação anterior ao teste de t − 20 e o Pix de R$ 2.000 saía
        approved. Na v5, Pix de até R$ 10 nunca são evidência de relação:
        o teste de t − 20 não tem relação antes dele e F10 dispara.
        Esperado: two_factor_required / floor_2fa (score 13).
        Quebra: approved (a implementação ainda conta centavos como relação).
        """
        response = self._run(seeder, (timedelta(minutes=40), timedelta(minutes=20)))
        assert_decision(response, "two_factor_required", "floor_2fa")


# =============================================================== 6

class TestF6TwoSourceAccounts:
    def test_same_cpf_two_accounts_split_night_limit(self, seeder):
        """Caso 6. R$ 800 + R$ 800 à noite, de duas contas do mesmo CPF (brecha fechada na v5).

        Na v5, F6 soma a exposição do PAGADOR na noite, em todas as contas de
        origem do mesmo documento. Primeiro Pix: R$ 800, approved (score 5).
        Segundo: R$ 1.600 na noite > R$ 1.000 → two_factor_required /
        floor_2fa (score 5). Na v4, que somava por conta, os dois saíam
        approved. O instante cai na noite civil de São Paulo e dentro da
        admissibilidade, qualquer que seja a hora do teste.
        Quebra: approved no segundo (F6 ainda agrega por conta).
        """
        known = Party(opening_date=now_utc() - timedelta(days=5 * 365))
        payer = mature_payer(seeder, known)
        receiver = Party(opening_date=now_utc() - timedelta(days=4 * 365))
        night = night_instant(now_utc())

        first = post(build_payload(new_id("pix-f6a"), payer, receiver, 80_000, night,
                                   source_branch="0001", source_account="11111"))
        assert_decision(first, "approved")
        second = post(build_payload(new_id("pix-f6b"), payer, receiver, 80_000, night + timedelta(minutes=1),
                                    source_branch="0001", source_account="22222"))
        assert_decision(second, "two_factor_required", "floor_2fa")


# =============================================================== 7

class TestPendingExposureSelfBlock:
    def test_pending_2fa_keeps_f9_window_full(self, seeder):
        """Caso 7. Um 2FA pendente conta na exposição (sem E1 ele nunca sai de PE).

        Passo 1: R$ 20.000,01 (teto da conta de 3 anos é R$ 20.000) → F9 →
        two_factor_required / floor_2fa.
        Passo 2, 2 min depois: R$ 5 para o destino de sempre. A janela de
        60 min soma R$ 20.000,06 > teto → F9 de novo → two_factor_required /
        floor_2fa, com score 0 de dia (5 à noite).
        Esperado pela v6 (seção 8, "efeitos de PE sem E1"). Quebra: approved
        no passo 2 (PE ficou fora da exposição) ou rejected em qualquer passo.
        """
        known = Party(opening_date=now_utc() - timedelta(days=5 * 365))
        payer = mature_payer(seeder, known)
        stranger = Party(opening_date=now_utc() - timedelta(days=4 * 365))
        t = now_utc()

        big = post(build_payload(new_id("pix-pe-big"), payer, stranger, 2_000_001, t - timedelta(minutes=2)))
        assert_decision(big, "two_factor_required", "floor_2fa")

        small = post(build_payload(new_id("pix-pe-small"), payer, known, 500, t))
        body = assert_decision(small, "two_factor_required", "floor_2fa")
        assert body["metadata"]["score"] <= 5


# =============================================================== 8

class TestS3MedianPoisoning:
    def _payer_with_history(self, seeder):
        known = Party(opening_date=now_utc() - timedelta(days=5 * 365))
        payer = mature_payer(seeder, known, count=14, amount=100_000, start_days=50, step=3)
        return payer, known

    def test_micro_payments_drag_median_down(self, seeder):
        """Caso 8. 14 Pix de R$ 1.000 (semeados) + 16 de R$ 5 (pela API), depois R$ 600.

        As 30 referências de S3 viram 16 × 500 e 14 × 100.000: mediana 500.
        R$ 600 ≥ R$ 500 e > 10 × R$ 5 → S3 +35. Com A3 −10 e A4 −7, D* = 17:
        score 18, approved. O controle (mesmo histórico, sem os micropagamentos)
        tem score 0 de dia e 5 à noite.
        Esperado pela v6: S3 mantém os Pix pequenos na mediana de propósito,
        porque é isso que pega o aquecimento com centavos (teste 12 da
        política); o custo é este, aceito como limitação. Quebra do teste =
        a mediana ignorou os micropagamentos (score igual ao controle).
        """
        payer, known = self._payer_with_history(seeder)
        control, _ = self._payer_with_history(seeder)
        t = now_utc()
        for i in range(16):
            micro = post(build_payload(new_id("pix-micro"), payer, known, 500,
                                       t - timedelta(hours=20) + timedelta(minutes=70 * i)))
            assert_decision(micro, "approved")

        stranger = Party(opening_date=now_utc() - timedelta(days=4 * 365))
        poisoned = post(build_payload(new_id("pix-s3-poisoned"), payer, stranger, 60_000, t))
        clean = post(build_payload(new_id("pix-s3-control"), control, stranger, 60_000, t))

        poisoned_body = assert_decision(poisoned, "approved")
        clean_body = assert_decision(clean, "approved")
        assert poisoned_body["metadata"]["score"] == 18, poisoned_body
        assert clean_body["metadata"]["score"] <= 5, clean_body
        assert poisoned_body["metadata"]["score"] - clean_body["metadata"]["score"] >= 13


# =============================================================== 9

class TestBlindIdempotency:
    def test_replay_with_tampered_payload_returns_frozen_answer(self, seeder):
        """Caso 9. Mesmo id, amount R$ 10 → R$ 999.999,99 e outra conta de destino.

        Esperado (v6, seção 7): 201 na primeira; 200 na segunda com o corpo
        idêntico ao da primeira, sem nova análise; GET igual; no banco, o
        amount e a conta de destino continuam os do passo 1.
        Quebra: 201 de novo, status diferente, ou payload sobrescrito.
        """
        payer, receiver, other = Party(), Party(), Party()
        pix_id = new_id("pix-idem")
        original = post(build_payload(pix_id, payer, receiver, 1_000, now_utc()))
        first = assert_decision(original, "approved")

        tampered = build_payload(pix_id, payer, other, 99_999_999, now_utc())
        replay = post(tampered)
        assert_not_500(replay)
        assert replay.response_status == 200, replay.response_json
        assert replay.response_json == first

        fetched = get(pix_id)
        assert fetched.response_status == 200
        assert {k: fetched.response_json[k] for k in ("key", "status", "metadata")} == first
        assert fetched.response_json["transaction"]["amount"] == 1_000

        stored = seeder.stored_transaction(pix_id)
        assert stored["amount"] == 1_000
        assert stored["account_number"] == receiver.account_number


# =============================================================== 10

class TestLockContention:
    def test_sixteen_concurrent_requests_same_payer(self, seeder):
        """Caso 10. 16 POST simultâneos do mesmo pagador, ids distintos.

        Esperado (v6, seção 11): as travas serializam o pagador; cada pedido
        responde 201 ou, se a trava passar de 2 s, 503 QIT001006. Nunca 500
        (pool do SQLAlchemy esgotado vira QIT000500). Reenviar os 503 com o
        mesmo id termina em 201, e o banco fica com exatamente 16 transações.
        """
        payer, receiver = Party(), Party()
        t = now_utc()
        payloads = [build_payload(f"pix-conc-{i}-{uuid.uuid4().hex[:8]}", payer, receiver, 1_000, t)
                    for i in range(1, 17)]

        with ThreadPoolExecutor(max_workers=16) as pool:
            responses = list(pool.map(post, payloads))

        for response in responses:
            assert response.response_status in (201, 503), (response.response_status, response.response_json)
            if response.response_status == 503:
                assert response.response_json["code"] == "QIT001006", response.response_json

        for payload, response in zip(payloads, responses):
            if response.response_status == 503:
                retry = post(payload)
                assert retry.response_status in (200, 201), retry.response_json

        for payload in payloads:
            assert get(payload["id"]).response_status == 200
        assert seeder.count_payer_transactions(payer.document) == 16
