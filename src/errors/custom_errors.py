from errors import QIException


class NotFoundAnalysis(QIException):
    code = "QIT001001"

    def __init__(self, transaction_id) -> None:
        title = "Analysis not Found"
        http_status = 404
        description = f"There is no analysis for the Pix with id {transaction_id}."
        translation = "Não existe análise para o Pix com este id."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidDocumentNumber(QIException):
    """O documento tem o formato certo e não existe.

    422, e não 400: o schema já cobrou o formato (máscara ou só dígitos,
    11 ou 14). Aqui a API leu, entendeu, e os dígitos verificadores não
    batem. A mensagem não repete o número: documento é dado pessoal, e a
    resposta de erro acaba em log.
    """

    code = "QIT001003"

    def __init__(self, field) -> None:
        title = "Invalid Document Number"
        http_status = 422
        description = f"The document number in {field} is not a valid CPF or CNPJ."
        translation = "O CPF ou CNPJ informado não é válido."
        super().__init__(title, self.code, http_status, description, translation)


class TransactionDateNotAdmissible(QIException):
    """transaction_date longe demais do relógio do servidor.

    Só para id novo: entre received_at − 24h e received_at + 5 min, com as
    duas fronteiras incluídas. Um Pix datado de ontem à noite ainda pode
    estar chegando atrasado; um datado da semana passada, não — e um
    datado do futuro distante poderia furar todas as janelas.
    """

    code = "QIT001004"

    def __init__(self) -> None:
        title = "Transaction date not admissible"
        http_status = 422
        description = "transaction_date must be between 24 hours before and 5 minutes after the server clock."
        translation = "A data da transação está fora da janela aceita (até 24h antes ou 5 min depois do recebimento)."
        super().__init__(title, self.code, http_status, description, translation)


class OpeningDateAfterTransaction(QIException):
    code = "QIT001005"

    def __init__(self, field) -> None:
        title = "Account opened after the transaction"
        http_status = 422
        description = f"{field}.opening_date is after transaction_date."
        translation = "A data de abertura da conta é posterior à data da transação."
        super().__init__(title, self.code, http_status, description, translation)


class DatabaseUnavailable(QIException):
    """Banco ou trava sem resposta: o motor não decide sem histórico.

    503, e nada foi gravado. Repetir o mesmo id faz a análise normalmente
    — a idempotência garante que ela acontece uma vez só.
    """

    code = "QIT001006"

    def __init__(self, reason) -> None:
        title = "Database unavailable"
        http_status = 503
        description = f"The analysis could not be completed: {reason}. Nothing was recorded."
        translation = "O banco não respondeu a tempo. Nada foi gravado; repita a requisição com o mesmo id."
        super().__init__(title, self.code, http_status, description, translation)


class TwoFactorFinalState(QIException):
    """E1: pediram um resultado diferente do que já está gravado.

    AU e NC são finais. Repetir o MESMO resultado devolve 200; pedir
    outro é conflito com o estado gravado — e uma decisão que não é 2FA
    também já é final.
    """

    code = "QIT001007"

    def __init__(self, current_state, requested_result) -> None:
        title = "Two factor already finished"
        http_status = 409
        description = f"The two factor is {current_state} and cannot receive {requested_result}."
        translation = "O 2FA desta análise já tem um resultado final diferente."
        super().__init__(title, self.code, http_status, description, translation)


class TwoFactorExpired(QIException):
    code = "QIT001008"

    def __init__(self) -> None:
        title = "Two factor expired"
        http_status = 409
        description = "The two factor deadline (received_at + 10 minutes) has passed."
        translation = "O prazo do 2FA (10 minutos após o recebimento) expirou."
        super().__init__(title, self.code, http_status, description, translation)


class CommitOutcomeUnknown(QIException):
    """A conexão caiu durante o COMMIT: não dá pra saber se gravou.

    Diferente do QIT001006, aqui talvez TENHA gravado. O cliente repete o
    mesmo id: se gravou, recebe 200 com a resposta congelada; se não, a
    análise é feita uma única vez.
    """

    code = "QIT001009"

    def __init__(self) -> None:
        title = "Commit outcome unknown"
        http_status = 503
        description = "The connection was lost during commit. Repeat the request with the same id."
        translation = "Não foi possível confirmar a gravação. Repita a requisição com o mesmo id."
        super().__init__(title, self.code, http_status, description, translation)
