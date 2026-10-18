"""As definições derivadas da seção 3.4, calculadas uma vez por análise.

Várias regras perguntam a mesma coisa — o dispositivo é confiável? o
destino é conhecido? qual é o teto da faixa? — e a resposta precisa ser
a MESMA para todas. Por isso ela é calculada aqui, uma vez, e entra na
fundamentação no bloco "derived".
"""
from datetime import datetime
from typing import List, Optional

from controllers.policy import parameters
from controllers.policy.history import (
    CurrentTransaction,
    History,
    HistoryEvent,
    account_age_days,
    default_order,
    iso,
)
from utils.civil_time import civil_day
from utils.geo import haversine_km

DEVICE_NEW = "new"
DEVICE_OBSERVED = "observed"
DEVICE_TRUSTED = "trusted"


class Facts:
    def __init__(self, current: CurrentTransaction, history: History) -> None:
        self.current = current
        self.history = history
        self.t = current.instant
        self.referenced_events = {}

        self.source_account_age = account_age_days(current.source_opening_date, self.t)
        self.destination_account_age = account_age_days(current.destination_opening_date, self.t)
        self.ceiling = self._ceiling()

        self.device_grade, self.device_summary = self._device_grade()
        self.habitual_locations = self._habitual_locations()
        self.is_mature, self.maturity_summary = self._maturity()
        self.relation_evidences = self._relation_evidences()
        self.is_known_destination = len(self.relation_evidences) > 0
        self.is_new_person = self._is_new_person()

    # ── Registro do que foi lido ─────────────────────────────────────
    def reference(self, events: List[HistoryEvent]) -> List[str]:
        """Marca os eventos como lidos (bloco "Estados lidos") e devolve os ids."""
        ids = []
        for event in events:
            self.referenced_events[event.transaction_id] = event
            ids.append(event.transaction_id)

        return ids

    # ── Recortes do histórico do pagador ─────────────────────────────
    def payer_events(self) -> List[HistoryEvent]:
        return self.history.payer_events

    def exposure_events(self) -> List[HistoryEvent]:
        exposure = []
        for event in self.history.payer_events:
            if event.is_exposure:
                exposure.append(event)

        return exposure

    def good_evidences_between(self, start: datetime, end: datetime) -> List[HistoryEvent]:
        """Evidências boas do pagador com instante em [start, end)."""
        evidences = []
        for event in self.history.payer_events:
            if event.is_good_evidence(self.t) and start <= event.instant < end:
                evidences.append(event)

        return evidences

    # ── Seção 4: teto da faixa ───────────────────────────────────────
    def _ceiling(self) -> int:
        ceiling = None
        for minimum_age, pf_ceiling, pj_ceiling in parameters.CEILING_BY_ACCOUNT_AGE:
            if self.source_account_age >= minimum_age:
                if self.current.payer_type == "PF":
                    ceiling = pf_ceiling
                else:
                    ceiling = pj_ceiling

        return ceiling

    # ── Dispositivo: novo, observado ou confiável ────────────────────
    def _device_grade(self):
        """Grau do dispositivo (pagador, device_id). RJ e PE não mudam o grau."""
        evidences = []
        for event in self.history.device_events:
            if event.is_good_evidence(self.t):
                evidences.append(event)

        days = set()
        first_instant = None
        for event in evidences:
            days.add(civil_day(event.instant))
            if first_instant is None or event.instant < first_instant:
                first_instant = event.instant

        if not evidences:
            grade = DEVICE_NEW
        elif (
            len(evidences) >= parameters.DEVICE_TRUSTED_MIN_EVIDENCES
            and len(days) >= parameters.DEVICE_TRUSTED_MIN_DAYS
            and first_instant <= self.t - parameters.DEVICE_TRUSTED_MIN_AGE
        ):
            grade = DEVICE_TRUSTED
        else:
            grade = DEVICE_OBSERVED

        summary = {
            "device_id": self.current.device_id,
            "good_evidences": len(evidences),
            "civil_days": len(days),
            "first_evidence": iso(first_instant),
            "grade": grade,
        }

        return grade, summary

    # ── Local habitual ───────────────────────────────────────────────
    def _habitual_locations(self) -> List[dict]:
        """Pontos de evidência boa em [t − 90 d, t) com vizinhos em ≥ 3 dias civis num raio de 30 km."""
        located = []
        for event in self.good_evidences_between(self.t - parameters.HABITUAL_LOCATION_WINDOW, self.t):
            if event.has_geo:
                located.append(event)

        habitual = []
        for point in located:
            neighbour_days = set()
            for other in located:
                distance = haversine_km(point.latitude, point.longitude, other.latitude, other.longitude)
                if distance <= parameters.HABITUAL_LOCATION_RADIUS_KM:
                    neighbour_days.add(civil_day(other.instant))

            if len(neighbour_days) >= parameters.HABITUAL_LOCATION_MIN_DAYS:
                habitual.append({
                    "event_id": point.transaction_id,
                    "latitude": point.latitude,
                    "longitude": point.longitude,
                    "civil_days": len(neighbour_days),
                })

        return habitual

    def distance_to_nearest_habitual(self) -> Optional[float]:
        if not self.current.has_geo or not self.habitual_locations:
            return None

        nearest = None
        for location in self.habitual_locations:
            distance = haversine_km(
                self.current.latitude, self.current.longitude, location["latitude"], location["longitude"]
            )
            if nearest is None or distance < nearest:
                nearest = distance

        return nearest

    # ── Maturidade de F2 ─────────────────────────────────────────────
    def _maturity(self):
        """Ao menos 5 evidências boas ≥ 2.000 em ao menos 3 dias civis, em todo o histórico."""
        evidences = []
        for event in self.history.maturity_events:
            if event.is_good_evidence(self.t) and event.amount >= parameters.ACTIVE_DAY_MIN_AMOUNT:
                evidences.append(event)

        days = set()
        for event in evidences:
            days.add(civil_day(event.instant))

        is_mature = (
            len(evidences) >= parameters.F2_MATURITY_MIN_EVIDENCES
            and len(days) >= parameters.F2_MATURITY_MIN_DAYS
        )

        return is_mature, {"good_evidences_2000": len(evidences), "civil_days": len(days), "mature": is_mature}

    # ── Relação com a conta de destino ───────────────────────────────
    def _relation_evidences(self) -> List[HistoryEvent]:
        evidences = []
        for event in self.history.relationship_events:
            if event.is_relation_evidence(self.t):
                evidences.append(event)

        return default_order(evidences)

    # ── Pessoa nova (F11) ────────────────────────────────────────────
    def _is_new_person(self) -> bool:
        first_instant = self.history.first_event_instant

        if first_instant is None:
            return True

        return self.t - first_instant < parameters.NEW_PERSON_WINDOW

    def summary(self) -> dict:
        return {
            "payer_type": self.current.payer_type,
            "destination_type": self.current.destination_type,
            "source_account_age_days": self.source_account_age,
            "destination_account_age_days": self.destination_account_age,
            "ceiling": self.ceiling,
            "device": self.device_summary,
            "habitual_locations": self.habitual_locations,
            "maturity": self.maturity_summary,
            "known_destination": self.is_known_destination,
            "new_person": self.is_new_person,
            "first_event_instant": iso(self.history.first_event_instant),
        }
