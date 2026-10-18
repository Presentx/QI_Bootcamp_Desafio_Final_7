import ipaddress
import re
from datetime import timedelta

from sqlalchemy.exc import DBAPIError, OperationalError

from constants import LOCK_TIMEOUT, POLICY_VERSION, TWO_FACTOR_ROUTE_ENABLED
from controllers.base_controller import BaseController
from controllers.policy import parameters
from controllers.policy.engine import evaluate
from controllers.policy.history import CurrentTransaction, History, HistoryEvent, state_at_cutoff
from dtos import PixTransactionDTO
from errors import (
    CommitOutcomeUnknown,
    DatabaseUnavailable,
    InvalidDocumentNumber,
    InvalidSchema,
    NotFoundAnalysis,
    OpeningDateAfterTransaction,
    TransactionDateNotAdmissible,
    TwoFactorExpired,
    TwoFactorFinalState,
)
from models import AnalysisStatus, PixTransaction
from repositories import AnalysisRepository, LockRepository, PixTransactionRepository
from utils import clock
from utils.document_number import document_type, is_valid_document, only_digits
from utils.request_context import get_request_id

TRANSACTION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")

ADMISSIBILITY_PAST = timedelta(hours=24)
ADMISSIBILITY_FUTURE = timedelta(minutes=5)

# O código do Postgres para "não consegui a trava a tempo" (lock_timeout).
LOCK_NOT_AVAILABLE = "55P03"


class PixTransactionController(BaseController):
    """O fluxo de decisão da seção 9: validar, travar, consultar, decidir, gravar.

    As regras antifraude em si moram em src/controllers/policy/, longe do
    banco: este controller junta o histórico pelas consultas do
    repository, entrega ao motor e grava o que ele decidiu. A ordem das
    etapas abaixo é a regra, e cada uma tem um porquê na docstring dela.
    """

    def __init__(self) -> None:
        super().__init__(__name__)
        self.pix_transaction_repository = PixTransactionRepository(self.context)
        self.analysis_repository = AnalysisRepository(self.context)
        self.lock_repository = LockRepository(self.context)

    # ────────────────────────────────────────────────────────────────
    # POST /pix/transaction
    # ────────────────────────────────────────────────────────────────
    def analyze(self, payload: dict) -> dict:
        """Analisa um Pix novo, ou devolve a resposta congelada de um id repetido.

        Tudo que não precisa de relógio nem de banco vem antes do BEGIN
        (estrutura e semântica invariável): um pedido torto nunca chega a
        pedir trava. Depois, as duas travas, SEMPRE nesta ordem — id e
        depois pagador — para que duas análises nunca esperem uma pela
        outra em ordem trocada.

        A idempotência é só pelo id. Repetir o id devolve 200 com a
        resposta congelada, sem admissibilidade e sem olhar o resto do
        conteúdo: o payload original permanece gravado e é o único que
        vale. É o que torna seguro o cliente repetir depois de um 503.
        """
        normalized = self._normalize(payload)

        try:
            self.lock_repository.set_lock_timeout(LOCK_TIMEOUT)
            self.lock_repository.acquire(f"id:{normalized['transaction_id']}")
            self.lock_repository.acquire(f"pagador:{normalized['payer_document']}")

            existing = self.pix_transaction_repository.get_by_transaction_id(normalized["transaction_id"])

            if existing is not None:
                frozen_response = existing.analysis.response
                self.session.commit()
                return {"response": frozen_response, "created": False}

            # O relógio do servidor é lido DEPOIS das travas: o received_at
            # é o corte do histórico, e sob a trava do pagador ele enxerga
            # tudo que esse pagador já gravou.
            received_at = clock.now()
            self._check_admissibility(normalized["event_instant"], received_at)

            current = self._current_transaction(normalized, received_at)
            history = self._load_history(current)
            result = evaluate(current, history, POLICY_VERSION, TWO_FACTOR_ROUTE_ENABLED)

            response = self._record(normalized, received_at, result)
        except DBAPIError as error:
            raise self._database_error(error)

        self._commit()

        return {"response": response, "created": True}

    # ────────────────────────────────────────────────────────────────
    # GET /pix/transaction/{id}
    # ────────────────────────────────────────────────────────────────
    def get_by_transaction_id(self, transaction_id: str) -> dict:
        pix_transaction = self.pix_transaction_repository.get_by_transaction_id(transaction_id.strip())

        if pix_transaction is None:
            raise NotFoundAnalysis(transaction_id)

        return PixTransactionDTO.consultation(pix_transaction.analysis, clock.now(), TWO_FACTOR_ROUTE_ENABLED)

    # ────────────────────────────────────────────────────────────────
    # PUT /pix/transaction/{id}/two_factor  (E1)
    # ────────────────────────────────────────────────────────────────
    def register_two_factor(self, transaction_id: str, result: str) -> dict:
        """PE → AU | NC, uma vez, dentro do prazo — sob as mesmas duas travas.

        A trava do pagador é o que serializa a confirmação com uma análise
        nova do mesmo pagador: a análise enxerga o estado vigente no corte
        dela, nem antes nem depois (testes 22-A e 22-B).

        Repetir o MESMO resultado devolve 200 igual, mesmo depois do prazo.
        Um resultado diferente de um estado final é 409 QIT001007; um PE
        cujo prazo passou é 409 QIT001008.
        """
        transaction_id = transaction_id.strip()

        try:
            self.lock_repository.set_lock_timeout(LOCK_TIMEOUT)
            self.lock_repository.acquire(f"id:{transaction_id}")

            pix_transaction = self.pix_transaction_repository.get_by_transaction_id(transaction_id)

            if pix_transaction is None:
                raise NotFoundAnalysis(transaction_id)

            self.lock_repository.acquire(f"pagador:{pix_transaction.payer_document}")

            analysis = pix_transaction.analysis
            now = clock.now()

            if analysis.status.enumerator != AnalysisStatus.TWO_FACTOR_REQUIRED:
                raise TwoFactorFinalState(analysis.status.enumerator, result)

            recorded = analysis.two_factor_event

            if recorded is not None:
                if recorded.result != result:
                    raise TwoFactorFinalState(recorded.result, result)
            else:
                if now > analysis.two_factor_deadline:
                    raise TwoFactorExpired()

                self.analysis_repository.create_two_factor_event(analysis, result, now, self._request_id())

            two_factor_dto = {
                "key": analysis.analysis_key,
                "status": analysis.status.enumerator,
                "two_factor": PixTransactionDTO.two_factor(analysis, now, TWO_FACTOR_ROUTE_ENABLED),
            }
        except DBAPIError as error:
            raise self._database_error(error)

        self._commit()

        return two_factor_dto

    # ────────────────────────────────────────────────────────────────
    # Validação e normalização (seção 2)
    # ────────────────────────────────────────────────────────────────
    def _normalize(self, payload: dict) -> dict:
        """O payload que o schema aceitou, virado o formato em que é gravado.

        O schema cobra quase toda a estrutura; o que ele não sabe fazer
        (um calendário de verdade, um IP de verdade) é cobrado aqui, com o
        mesmo 400. Depois vem a semântica invariável, com 422: os dígitos
        verificadores e a abertura das contas.
        """
        transaction_id = payload["id"].strip()

        if not TRANSACTION_ID_PATTERN.fullmatch(transaction_id):
            raise InvalidSchema("id must have 1 to 64 characters [A-Za-z0-9_-]")

        event_instant = self._parse_date(payload["transaction_date"], "transaction_date")

        source_account = payload["source_account"]
        destination_account = payload["destination_account"]
        source = payload["source"]

        source_opening_date = self._parse_date(source_account["opening_date"], "source_account.opening_date")
        destination_opening_date = self._parse_date(
            destination_account["opening_date"], "destination_account.opening_date"
        )

        try:
            ip = str(ipaddress.ip_address(source["ip"]))
        except ValueError:
            raise InvalidSchema("source.ip must be an IPv4 or IPv6 address")

        latitude = None
        longitude = None
        geolocation = source.get("geolocation")

        if geolocation is not None:
            latitude = round(float(geolocation["latitude"]), 6)
            longitude = round(float(geolocation["longitude"]), 6)

            # (0, 0) é o "não sei" de muito aparelho: convenção da simulação [SIM].
            if latitude == 0 and longitude == 0:
                latitude = None
                longitude = None

        payer_document = only_digits(source_account["owner"]["document_number"])
        destination_document = only_digits(destination_account["owner"]["document_number"])

        if not is_valid_document(payer_document):
            raise InvalidDocumentNumber("source_account.owner")

        if not is_valid_document(destination_document):
            raise InvalidDocumentNumber("destination_account.owner")

        if source_opening_date > event_instant:
            raise OpeningDateAfterTransaction("source_account")

        if destination_opening_date > event_instant:
            raise OpeningDateAfterTransaction("destination_account")

        return {
            "transaction_id": transaction_id,
            "amount": int(payload["amount"]),
            "event_instant": event_instant,
            "payer_document": payer_document,
            "payer_type": document_type(payer_document),
            "source_branch": source_account["branch"],
            "source_account": source_account["account_number"],
            "source_opening_date": source_opening_date,
            "destination_participant": destination_account["participant"],
            "destination_branch": destination_account["branch"],
            "destination_account": destination_account["account_number"],
            "destination_opening_date": destination_opening_date,
            "destination_document": destination_document,
            "destination_type": document_type(destination_document),
            "device_id": source["device_id"],
            "channel": source["channel"],
            "ip": ip,
            "latitude": latitude,
            "longitude": longitude,
        }

    def _parse_date(self, raw_date: str, field: str):
        instant = clock.parse_instant(raw_date)

        if instant is None:
            raise InvalidSchema(f"{field} must be an ISO 8601 date-time with offset")

        return instant

    def _check_admissibility(self, event_instant, received_at) -> None:
        """received_at − 24h ≤ transaction_date ≤ received_at + 5 min, fronteiras inclusivas."""
        if not (received_at - ADMISSIBILITY_PAST <= event_instant <= received_at + ADMISSIBILITY_FUTURE):
            raise TransactionDateNotAdmissible()

    # ────────────────────────────────────────────────────────────────
    # Histórico (seção 10): cada regra com a sua consulta, todas com o corte
    # ────────────────────────────────────────────────────────────────
    def _current_transaction(self, normalized: dict, received_at) -> CurrentTransaction:
        return CurrentTransaction(
            transaction_id=normalized["transaction_id"],
            amount=normalized["amount"],
            instant=normalized["event_instant"],
            received_at=received_at,
            payer_document=normalized["payer_document"],
            payer_type=normalized["payer_type"],
            source_opening_date=normalized["source_opening_date"],
            destination=(
                normalized["destination_participant"],
                normalized["destination_branch"],
                normalized["destination_account"],
            ),
            destination_opening_date=normalized["destination_opening_date"],
            destination_document=normalized["destination_document"],
            destination_type=normalized["destination_type"],
            device_id=normalized["device_id"],
            latitude=normalized["latitude"],
            longitude=normalized["longitude"],
        )

    def _load_history(self, current: CurrentTransaction) -> History:
        repository = self.pix_transaction_repository
        payer = current.payer_document
        t = current.instant
        cutoff = current.received_at
        limit = parameters.FULL_HISTORY_LIMIT

        history = History(cutoff=cutoff)

        history.payer_events = self._to_events(
            repository.list_payer_window(
                payer, t - parameters.PAYER_WINDOW_BACK, t + parameters.PAYER_WINDOW_FORWARD, cutoff
            ),
            cutoff,
        )
        history.maturity_events = self._to_events(
            repository.list_payer_maturity_candidates(payer, parameters.ACTIVE_DAY_MIN_AMOUNT, t, cutoff, limit),
            cutoff,
        )
        history.relationship_events = self._to_events(
            repository.list_relationship(payer, current.destination, t, cutoff, limit), cutoff
        )
        history.device_events = self._to_events(
            repository.list_device(payer, current.device_id, t, cutoff, limit), cutoff
        )
        history.recipient_events = self._to_events(
            repository.list_recipient(payer, current.destination_document, t, cutoff, limit), cutoff
        )

        previous_geo = repository.get_previous_with_geolocation(payer, t, cutoff)
        if previous_geo is not None:
            history.previous_geo_event = self._to_event(previous_geo, cutoff)

        history.first_event_instant = repository.get_first_event_instant(payer, cutoff)
        history.destination_new_payers = repository.list_destination_new_payers(
            current.destination, t - parameters.S8_WINDOW, t, cutoff
        )

        return history

    def _to_events(self, rows: list, cutoff) -> list:
        events = []
        for row in rows:
            events.append(self._to_event(row, cutoff))

        return events

    def _to_event(self, row: PixTransaction, cutoff) -> HistoryEvent:
        analysis = row.analysis
        two_factor_event = analysis.two_factor_event

        two_factor_result = None
        two_factor_recorded_at = None
        if two_factor_event is not None:
            two_factor_result = two_factor_event.result
            two_factor_recorded_at = two_factor_event.recorded_at

        state, state_since = state_at_cutoff(
            analysis.status.enumerator,
            row.received_at,
            analysis.two_factor_deadline,
            two_factor_result,
            two_factor_recorded_at,
            cutoff,
            TWO_FACTOR_ROUTE_ENABLED,
        )

        latitude = None
        longitude = None
        if row.latitude is not None:
            latitude = float(row.latitude)
            longitude = float(row.longitude)

        return HistoryEvent(
            transaction_id=row.transaction_id,
            amount=row.amount,
            instant=row.event_instant,
            received_at=row.received_at,
            device_id=row.device_id,
            destination=(row.destination_participant, row.destination_branch, row.destination_account),
            destination_document=row.destination_document,
            destination_type=row.destination_type,
            latitude=latitude,
            longitude=longitude,
            state=state,
            state_since=state_since,
            independent_rejection=analysis.independent_rejection,
        )

    # ────────────────────────────────────────────────────────────────
    # Gravação: transação, análise e fundamentação no mesmo COMMIT
    # ────────────────────────────────────────────────────────────────
    def _record(self, normalized: dict, received_at, result: dict) -> dict:
        transaction_data = dict(normalized)
        transaction_data["received_at"] = received_at
        transaction_data["payload"] = self._payload_to_store(normalized)

        pix_transaction = self.pix_transaction_repository.create(transaction_data)

        two_factor_deadline = None
        if result["status"] == AnalysisStatus.TWO_FACTOR_REQUIRED:
            two_factor_deadline = received_at + parameters.TWO_FACTOR_DEADLINE

        analysis = self.analysis_repository.create(
            pix_transaction,
            {
                "status": result["status"],
                "score": result["score"],
                "score_band": result["score_band"],
                "decision_basis": result["decision_basis"],
                "independent_rejection": result["independent_rejection"],
                "policy_version": POLICY_VERSION,
                "two_factor_deadline": two_factor_deadline,
                "reasoning": result["reasoning"],
                "request_id": self._request_id(),
            },
        )

        # A resposta é montada UMA vez e gravada: daqui pra frente ela só
        # é lida, nunca recalculada.
        response = PixTransactionDTO.response(analysis)
        self.analysis_repository.set_response(analysis, response)

        return response

    def _payload_to_store(self, normalized: dict) -> dict:
        stored = {}
        for field, value in normalized.items():
            if hasattr(value, "isoformat"):
                stored[field] = clock.iso(value)
            else:
                stored[field] = value

        return stored

    def _request_id(self) -> str:
        return get_request_id()

    def _commit(self) -> None:
        """A resposta de sucesso só sai depois do COMMIT confirmado (seção 9, Falhas)."""
        try:
            self.session.commit()
        except OperationalError as error:
            if error.connection_invalidated:
                raise CommitOutcomeUnknown()

            raise DatabaseUnavailable("commit was rolled back")
        except DBAPIError:
            raise DatabaseUnavailable("commit was rolled back")

    def _database_error(self, error: DBAPIError) -> DatabaseUnavailable:
        if getattr(error.orig, "pgcode", None) == LOCK_NOT_AVAILABLE:
            self.logger.warning("Trava não obtida em %s", LOCK_TIMEOUT)
            return DatabaseUnavailable("lock not acquired in time")

        self.logger.error(f"Erro de banco durante a análise: {error}")
        return DatabaseUnavailable("database error")
