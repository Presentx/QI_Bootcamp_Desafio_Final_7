from datetime import datetime
from typing import Optional

from models import Analysis
from utils.clock import iso


class PixTransactionDTO:
    """O que sai da API sobre um Pix analisado.

    Duas saídas, e a diferença entre elas é o assunto deste arquivo:

      • a RESPOSTA do contrato — {key, status, metadata} — que o POST
        devolve e que fica congelada na análise. Ela é montada uma vez, no
        `response`, e depois só é lida do banco: o POST repetido e o GET
        devolvem os mesmos bytes, mesmo que a política mude amanhã;
      • a CONSULTA do GET, que é a resposta congelada mais, à parte, o
        que é interno: a fundamentação, o payload original e o estado do
        2FA. O contrato fica intacto; o resto vem ao lado.
    """

    @staticmethod
    def response(analysis: Analysis) -> dict:
        return {
            "key": analysis.analysis_key,
            "status": analysis.status.enumerator,
            "metadata": {
                "score": analysis.score,
                "score_band": analysis.score_band,
                "decision_basis": analysis.decision_basis,
                "policy_version": analysis.policy_version,
            },
        }

    @staticmethod
    def two_factor(analysis: Analysis, now: datetime, route_enabled: bool) -> Optional[dict]:
        """O estado do 2FA AGORA — não no corte de nenhuma análise.

        PE até o resultado; AU ou NC depois dele, com a data. Com a E1
        ligada, o PE sem resposta vira NC (expirado) a partir do prazo.
        """
        if analysis.status.enumerator != "two_factor_required":
            return None

        event = analysis.two_factor_event

        if event is not None:
            state = "NC"
            if event.result == "confirmed":
                state = "AU"

            return {
                "state": state,
                "result": event.result,
                "since": iso(event.recorded_at),
                "deadline": iso(analysis.two_factor_deadline),
            }

        if route_enabled and now > analysis.two_factor_deadline:
            return {"state": "NC", "result": "expired", "since": iso(analysis.two_factor_deadline), "deadline": iso(analysis.two_factor_deadline)}

        return {"state": "PE", "result": None, "since": None, "deadline": iso(analysis.two_factor_deadline)}

    @staticmethod
    def consultation(analysis: Analysis, now: datetime, route_enabled: bool) -> dict:
        dto = dict(analysis.response)
        dto["transaction"] = analysis.pix_transaction.payload
        dto["two_factor"] = PixTransactionDTO.two_factor(analysis, now, route_enabled)
        dto["independent_rejection"] = analysis.independent_rejection
        dto["reasoning"] = analysis.reasoning

        return dto
