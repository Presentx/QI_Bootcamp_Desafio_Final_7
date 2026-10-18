from datetime import timedelta

from tests.utils.payload_generator import SAO_PAULO, PayloadGenerator
from tests.utils.random_generator import RandomGenerator
from tests.utils.request_generator import RequestGenerator
from tests.utils.time_utils import at, instant, to_iso

KEEP = object()


class Destination:
    """Uma conta de destino: participante, agência, conta, titular e abertura."""

    def __init__(self, kind: str = "PF", opened: str = "2020-01-01 10:00", document: str = None) -> None:
        if document is None:
            if kind == "PF":
                document = RandomGenerator.generate_cpf()
            else:
                document = RandomGenerator.generate_cnpj()

        self.document = document
        self.opened = opened
        self.participant = RandomGenerator.generate_digits(8)
        self.branch = RandomGenerator.generate_digits(4)
        self.account = RandomGenerator.generate_digits(8)


class Payer:
    """Um pagador, e o jeito de montar o histórico dele PELA API.

    O histórico da matriz de testes tem meses. Cada Pix dele entra por
    POST, como entraria em produção, com o relógio do servidor fixado
    pelo cabeçalho X-Test-Clock em `received` (por padrão, 1 segundo
    depois do `transaction_date`). Nada é escrito direto no banco.
    """

    def __init__(
        self,
        kind: str = "PF",
        opened: str = "2023-10-01 10:00",
        device: str = "D1",
        geolocation: dict = SAO_PAULO,
        document: str = None,
    ) -> None:
        if document is None:
            if kind == "PF":
                document = RandomGenerator.generate_cpf()
            else:
                document = RandomGenerator.generate_cnpj()

        self.document = document
        self.opened = opened
        self.account = RandomGenerator.generate_digits(8)
        self.device = device
        self.geolocation = geolocation

    def payload(self, when: str, amount: int, destination: Destination, device=None, geolocation=KEEP, account=None) -> dict:
        if device is None:
            device = self.device

        if geolocation is KEEP:
            geolocation = self.geolocation

        if account is None:
            account = self.account

        return PayloadGenerator.create_pix_payload(
            amount=amount,
            transaction_date=at(when),
            payer_document=self.document,
            source_account=account,
            source_opening_date=at(self.opened),
            destination_participant=destination.participant,
            destination_branch=destination.branch,
            destination_account=destination.account,
            destination_opening_date=at(destination.opened),
            destination_document=destination.document,
            device_id=device,
            geolocation=geolocation,
        )

    def pay(self, when: str, amount: int, destination: Destination, received: str = None, **options):
        """Manda um Pix e devolve (status, resposta, id)."""
        payload = self.payload(when, amount, destination, **options)

        if received is None:
            clock = to_iso(instant(when) + timedelta(seconds=1))
        else:
            clock = at(received)

        status, response = RequestGenerator.POST_pix_transaction(payload, clock=clock)
        return status, response, payload["id"]

    def seed(self, when: str, amount: int, destination: Destination, **options) -> str:
        """Um Pix do histórico que precisa terminar como evidência boa (AP ou AU).

        Se o motor pedir 2FA, o cliente confirma na hora (E1): o evento vira
        AU, que vale como AP para tudo que olha o passado. Rejeição num
        evento de histórico é erro de montagem do cenário, e o teste para.
        """
        status, response, transaction_id = self.pay(when, amount, destination, **options)
        assert status == 201, response

        if response["status"] == "two_factor_required":
            confirm_clock = to_iso(instant(when) + timedelta(seconds=2))
            status, confirmation = RequestGenerator.PUT_two_factor(transaction_id, "confirmed", clock=confirm_clock)
            assert status == 200, confirmation
        else:
            assert response["status"] == "approved", f"seed {when} {amount}: {response}"

        return transaction_id

    def seed_many(self, moments: list, amount: int, destination: Destination, **options) -> list:
        transaction_ids = []
        for when in moments:
            transaction_ids.append(self.seed(when, amount, destination, **options))

        return transaction_ids


class ObjectGenerator:
    @staticmethod
    def m30_payer(amount: int = 10_000) -> tuple:
        """O perfil M30 da matriz: PF com conta de 01/10/2023 e 30 Pix para Y
        (PF, conta de 2020) de 01/08 a 30/08 às 12:00, dispositivo D1, em SP.

        Resulta em pagador maduro, D1 confiável, local habitual em SP, 30 dias
        ativos (A4 = 15) e fora da rampa F11. Com amount=500_000 é o "M30 alto".
        """
        payer = Payer()
        y = Destination()

        moments = []
        for day in range(1, 31):
            moments.append(f"2026-08-{day:02d} 12:00")

        payer.seed_many(moments, amount, y)

        return payer, y

    @staticmethod
    def analysis(transaction_id: str) -> dict:
        """A consulta completa (GET), com a fundamentação."""
        status, response = RequestGenerator.GET_pix_transaction(transaction_id)
        assert status == 200, response
        return response

    @staticmethod
    def fired_floors(transaction_id: str) -> list:
        return ObjectGenerator.analysis(transaction_id)["reasoning"]["decision"]["fired_floors"]

    @staticmethod
    def fired_rules(transaction_id: str) -> dict:
        """{código: pontos} das regras de score que contaram (depois das supressões)."""
        fired = {}
        for rule in ObjectGenerator.analysis(transaction_id)["reasoning"]["rules"]:
            if rule["points"] > 0:
                fired[rule["code"]] = rule["points"]

        return fired

    @staticmethod
    def attenuators(transaction_id: str) -> dict:
        applied = {}
        for attenuator in ObjectGenerator.analysis(transaction_id)["reasoning"]["attenuators"]:
            if attenuator["discount"] > 0:
                applied[attenuator["code"]] = attenuator["discount"]

        return applied
