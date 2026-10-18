"""O que o motor sabe: a transação atual e o histórico, já no estado do corte.

Nada aqui fala com o banco. Quem consulta é o repository, quem traduz a
linha do banco num `HistoryEvent` é o controller (com `state_at_cutoff`),
e o motor só recebe listas prontas. Isso deixa cada regra testável no
papel: dado este histórico, este é o resultado.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from controllers.policy import parameters
from utils.clock import iso

# Os cinco estados da seção 3.2.
APPROVED_STATE = "AP"
REJECTED_STATE = "RJ"
PENDING_STATE = "PE"
AUTHENTICATED_STATE = "AU"
NOT_CONFIRMED_STATE = "NC"

EXPOSURE_STATES = (APPROVED_STATE, PENDING_STATE, AUTHENTICATED_STATE)
GOOD_EVIDENCE_STATES = (APPROVED_STATE, AUTHENTICATED_STATE)


def state_at_cutoff(
    decision: str,
    received_at: datetime,
    two_factor_deadline: Optional[datetime],
    two_factor_result: Optional[str],
    two_factor_recorded_at: Optional[datetime],
    cutoff: datetime,
    two_factor_route_enabled: bool,
) -> Tuple[str, datetime]:
    """O estado de um evento no corte da análise atual, e desde quando.

    approved e rejected são finais desde a decisão. Um two_factor_required
    é PE até haver resultado GRAVADO ANTES do corte: um AU confirmado
    depois do received_at desta análise ainda é PE para ela (teste 26c).
    Com a E1 ligada, um PE sem resposta vira NC a partir do prazo.
    """
    if decision == "approved":
        return APPROVED_STATE, received_at

    if decision == "rejected":
        return REJECTED_STATE, received_at

    if two_factor_recorded_at is not None and two_factor_recorded_at < cutoff:
        if two_factor_result == "confirmed":
            return AUTHENTICATED_STATE, two_factor_recorded_at

        return NOT_CONFIRMED_STATE, two_factor_recorded_at

    if two_factor_route_enabled and two_factor_deadline is not None and two_factor_deadline < cutoff:
        return NOT_CONFIRMED_STATE, two_factor_deadline

    return PENDING_STATE, received_at


@dataclass
class CurrentTransaction:
    """A transação em análise, normalizada."""

    transaction_id: str
    amount: int
    instant: datetime
    received_at: datetime
    payer_document: str
    payer_type: str
    source_opening_date: datetime
    destination: Tuple[str, str, str]
    destination_opening_date: datetime
    destination_document: str
    destination_type: str
    device_id: str
    latitude: Optional[float]
    longitude: Optional[float]

    @property
    def has_geo(self) -> bool:
        return self.latitude is not None


@dataclass
class HistoryEvent:
    """Um Pix já analisado, visto do corte da análise atual."""

    transaction_id: str
    amount: int
    instant: datetime
    received_at: datetime
    device_id: str
    destination: Tuple[str, str, str]
    destination_document: str
    destination_type: str
    latitude: Optional[float]
    longitude: Optional[float]
    state: str
    state_since: datetime
    independent_rejection: bool

    @property
    def has_geo(self) -> bool:
        return self.latitude is not None

    @property
    def is_exposure(self) -> bool:
        return self.state in EXPOSURE_STATES

    def is_good_evidence(self, t: datetime) -> bool:
        """Evidência boa: AP ou AU no corte E instante anterior a t."""
        return self.state in GOOD_EVIDENCE_STATES and self.instant < t

    def is_relation_evidence(self, t: datetime) -> bool:
        """Evidência de relação: evidência boa ACIMA de 1.000 (um teste de F10 não conta)."""
        return self.is_good_evidence(t) and self.amount > parameters.RELATION_EVIDENCE_MIN_AMOUNT

    def is_independent_rejection(self) -> bool:
        return self.state == REJECTED_STATE and self.independent_rejection


@dataclass
class History:
    """O que as consultas da seção 10 devolveram, todas com o corte por recebimento.

    payer_events            janela [t − 181 d, t + 1 d] do pagador, todos os estados
    maturity_events         histórico completo do pagador, instante < t, valor ≥ 2.000, não rejeitados (F2)
    relationship_events     pagador → conta de destino, instante ≤ t, todos os estados
    device_events           pagador + device_id, instante < t, todos os estados
    recipient_events        pagador → documento do recebedor, instante < t (F4)
    previous_geo_event      o evento com geolocalização anterior a t, o primeiro na ordenação padrão (S10)
    first_event_instant     o primeiro evento do pagador em qualquer estado (F11)
    destination_new_payers  pagador → instante do primeiro evento não rejeitado para a conta,
                            só os que caem em (t − 24h, t] (S8)
    """

    cutoff: datetime
    payer_events: List[HistoryEvent] = field(default_factory=list)
    maturity_events: List[HistoryEvent] = field(default_factory=list)
    relationship_events: List[HistoryEvent] = field(default_factory=list)
    device_events: List[HistoryEvent] = field(default_factory=list)
    recipient_events: List[HistoryEvent] = field(default_factory=list)
    previous_geo_event: Optional[HistoryEvent] = None
    first_event_instant: Optional[datetime] = None
    destination_new_payers: Dict[str, datetime] = field(default_factory=dict)


def default_order(events: List[HistoryEvent]) -> List[HistoryEvent]:
    """Ordenação padrão: instante decrescente, received_at decrescente, id crescente."""
    by_id = sorted(events, key=lambda event: event.transaction_id)
    return sorted(by_id, key=lambda event: (event.instant, event.received_at), reverse=True)


def median(values: List[int]) -> int:
    """Valor central; com quantidade par, média dos dois centrais arredondada para baixo."""
    ordered = sorted(values)
    middle = len(ordered) // 2

    if len(ordered) % 2 == 1:
        return ordered[middle]

    return (ordered[middle - 1] + ordered[middle]) // 2


def account_age_days(opening_date: datetime, t: datetime) -> int:
    """Dias completos entre a abertura e o instante da transação."""
    return (t - opening_date).days


def max_window(
    events: List[HistoryEvent],
    t: datetime,
    width: timedelta,
    current_value: int,
    value_of: Callable[[HistoryEvent], int],
) -> dict:
    """A janela máxima de duração `width` que contém t (seção 3.3).

    Avalia (X − W, X] para X = t e para X = instante de cada evento em
    (t, t + W), e fica com o maior total. A transação atual entra uma vez
    em cada janela — todas contêm t. Em tráfego em ordem só existe X = t;
    um evento atrasado passa a enxergar os eventos datados depois dele.
    """
    candidates = [t]
    for event in events:
        if t < event.instant < t + width:
            candidates.append(event.instant)

    best = None
    for window_end in candidates:
        window_start = window_end - width

        members = []
        for event in events:
            if window_start < event.instant <= window_end:
                members.append(event)

        total = current_value
        for event in members:
            total = total + value_of(event)

        if best is None or total > best["value"]:
            best = {
                "start": iso(window_start),
                "end": iso(window_end),
                "x": iso(window_end),
                "value": total,
                "event_ids": [event.transaction_id for event in members],
            }

    return best
