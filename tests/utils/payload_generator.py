from tests.utils.random_generator import RandomGenerator

SAO_PAULO = {"latitude": -23.5505, "longitude": -46.6333}
RIO_DE_JANEIRO = {"latitude": -22.9068, "longitude": -43.1729}
RECIFE = {"latitude": -8.0476, "longitude": -34.8770}


class PayloadGenerator:
    @staticmethod
    def create_pix_payload(
        transaction_id: str = None,
        amount: int = 10_000,
        transaction_date: str = "2026-10-17T14:00:00-03:00",
        payer_document: str = None,
        source_branch: str = "0001",
        source_account: str = None,
        source_opening_date: str = "2023-10-01T10:00:00-03:00",
        destination_participant: str = "60701190",
        destination_branch: str = "3675",
        destination_account: str = None,
        destination_opening_date: str = "2020-01-01T10:00:00-03:00",
        destination_document: str = None,
        device_id: str = "D1",
        channel: str = "app",
        ip: str = "198.51.100.23",
        geolocation: dict = SAO_PAULO,
    ) -> dict:
        """Um Pix válido no formato do contrato, com qualquer campo trocado a pedido.

        Sem argumento, o que precisa ser único sai aleatório (id, documentos,
        contas): dois testes nunca dividem pagador nem destino sem querer.
        `geolocation=None` deixa o campo de fora — ele é opcional.
        """
        if transaction_id is None:
            transaction_id = RandomGenerator.generate_transaction_id()

        if payer_document is None:
            payer_document = RandomGenerator.generate_cpf()

        if source_account is None:
            source_account = RandomGenerator.generate_digits(8)

        if destination_account is None:
            destination_account = RandomGenerator.generate_digits(8)

        if destination_document is None:
            destination_document = RandomGenerator.generate_cpf()

        source = {"device_id": device_id, "channel": channel, "ip": ip}
        if geolocation is not None:
            source["geolocation"] = dict(geolocation)

        return {
            "id": transaction_id,
            "amount": amount,
            "transaction_date": transaction_date,
            "source_account": {
                "branch": source_branch,
                "account_number": source_account,
                "opening_date": source_opening_date,
                "owner": {"document_number": payer_document},
            },
            "destination_account": {
                "participant": destination_participant,
                "branch": destination_branch,
                "account_number": destination_account,
                "opening_date": destination_opening_date,
                "owner": {"document_number": destination_document},
            },
            "source": source,
        }
