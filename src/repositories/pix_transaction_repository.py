from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func

from database import Context
from models import Analysis, AnalysisStatus, PixTransaction

NOT_REJECTED = [AnalysisStatus.APPROVED, AnalysisStatus.TWO_FACTOR_REQUIRED]


class PixTransactionRepository:
    """As consultas do histórico. Só aqui existe query.

    Toda consulta de histórico recebe o `cutoff` — o received_at da
    análise atual — e só enxerga eventos recebidos ANTES dele (seção 3.4).
    Sob a trava do pagador, isso é todo o passado dele; sem o corte, uma
    análise concorrente de OUTRO pagador poderia aparecer pela metade.

    Nenhuma regra mora aqui: estas funções devolvem linhas, e quem decide
    o estado de cada uma no corte e o que ele significa é o controller.
    """

    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create(self, transaction_data: dict) -> PixTransaction:
        pix_transaction = PixTransaction()

        for column, value in transaction_data.items():
            setattr(pix_transaction, column, value)

        self.session.add(pix_transaction)
        return pix_transaction

    def get_by_transaction_id(self, transaction_id: str) -> Optional[PixTransaction]:
        return self.session.query(PixTransaction).filter(PixTransaction.transaction_id == transaction_id).first()

    def _history(self, payer_document: str, cutoff: datetime):
        return self.session.query(PixTransaction).filter(
            PixTransaction.payer_document == payer_document,
            PixTransaction.received_at < cutoff,
        )

    def _not_rejected(self, query):
        return query.join(Analysis, Analysis.pix_transaction_id == PixTransaction.id).join(
            AnalysisStatus, AnalysisStatus.id == Analysis.status_id
        ).filter(AnalysisStatus.enumerator.in_(NOT_REJECTED))

    def list_payer_window(self, payer_document: str, start: datetime, end: datetime, cutoff: datetime) -> List[PixTransaction]:
        """Todos os eventos do pagador com instante em [start, end), em qualquer estado."""
        query = self._history(payer_document, cutoff).filter(
            PixTransaction.event_instant >= start,
            PixTransaction.event_instant < end,
        )
        return query.all()

    def list_payer_maturity_candidates(
        self, payer_document: str, minimum_amount: int, t: datetime, cutoff: datetime, limit: int
    ) -> List[PixTransaction]:
        """Histórico completo do pagador para a maturidade de F2: não rejeitados, valor mínimo, antes de t."""
        query = self._not_rejected(self._history(payer_document, cutoff)).filter(
            PixTransaction.amount >= minimum_amount,
            PixTransaction.event_instant < t,
        )
        return query.order_by(PixTransaction.event_instant.desc()).limit(limit).all()

    def list_relationship(
        self, payer_document: str, destination: tuple, t: datetime, cutoff: datetime, limit: int
    ) -> List[PixTransaction]:
        """Pagador → conta de destino, instante até t, em qualquer estado."""
        participant, branch, account = destination
        query = self._history(payer_document, cutoff).filter(
            PixTransaction.destination_participant == participant,
            PixTransaction.destination_branch == branch,
            PixTransaction.destination_account == account,
            PixTransaction.event_instant <= t,
        )
        return query.order_by(PixTransaction.event_instant.desc()).limit(limit).all()

    def list_device(self, payer_document: str, device_id: str, t: datetime, cutoff: datetime, limit: int) -> List[PixTransaction]:
        query = self._history(payer_document, cutoff).filter(
            PixTransaction.device_id == device_id,
            PixTransaction.event_instant < t,
        )
        return query.order_by(PixTransaction.event_instant.desc()).limit(limit).all()

    def list_recipient(
        self, payer_document: str, destination_document: str, t: datetime, cutoff: datetime, limit: int
    ) -> List[PixTransaction]:
        query = self._not_rejected(self._history(payer_document, cutoff)).filter(
            PixTransaction.destination_document == destination_document,
            PixTransaction.event_instant < t,
        )
        return query.order_by(PixTransaction.event_instant.desc()).limit(limit).all()

    def get_previous_with_geolocation(self, payer_document: str, t: datetime, cutoff: datetime) -> Optional[PixTransaction]:
        """O primeiro na ordenação padrão: instante desc, received_at desc, id asc (LIMIT 1)."""
        query = self._history(payer_document, cutoff).filter(
            PixTransaction.latitude.isnot(None),
            PixTransaction.event_instant < t,
        )
        return query.order_by(
            PixTransaction.event_instant.desc(),
            PixTransaction.received_at.desc(),
            PixTransaction.transaction_id.asc(),
        ).first()

    def get_first_event_instant(self, payer_document: str, cutoff: datetime) -> Optional[datetime]:
        return (
            self.session.query(func.min(PixTransaction.event_instant))
            .filter(PixTransaction.payer_document == payer_document, PixTransaction.received_at < cutoff)
            .scalar()
        )

    def list_destination_new_payers(
        self, destination: tuple, window_start: datetime, t: datetime, cutoff: datetime
    ) -> Dict[str, datetime]:
        """Pagadores cujo PRIMEIRO evento não rejeitado para a conta cai em (window_start, t] (S8).

        Esta consulta não tem trava: agregado por conta de destino não é
        protegido pela trava do pagador. Duas análises simultâneas de
        pagadores diferentes não se enxergam, e N pode sair subestimado
        durante a corrida — nunca contado em dobro (seção 11).
        """
        participant, branch, account = destination
        first_instant = func.min(PixTransaction.event_instant)

        query = self._not_rejected(
            self.session.query(PixTransaction.payer_document, first_instant).filter(
                PixTransaction.destination_participant == participant,
                PixTransaction.destination_branch == branch,
                PixTransaction.destination_account == account,
                PixTransaction.event_instant <= t,
                PixTransaction.received_at < cutoff,
            )
        )
        rows = query.group_by(PixTransaction.payer_document).having(first_instant > window_start).all()

        new_payers = {}
        for payer_document, instant in rows:
            new_payers[payer_document] = instant

        return new_payers
