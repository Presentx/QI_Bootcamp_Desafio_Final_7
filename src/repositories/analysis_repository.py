from uuid import uuid4

from database import Context
from models import Analysis, AnalysisStatus, PixTransaction, TwoFactorEvent


class AnalysisRepository:
    """A análise e o resultado do 2FA. Só aqui existe query."""

    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create(self, pix_transaction: PixTransaction, analysis_data: dict) -> Analysis:
        analysis = Analysis()

        # O UUID nasce aqui, no repository: é a `key` que sai na resposta.
        # O `id` numérico nunca sai da API.
        analysis.analysis_key = str(uuid4())
        analysis.pix_transaction = pix_transaction
        analysis.status = self.get_status(analysis_data["status"])
        analysis.score = analysis_data["score"]
        analysis.score_band = analysis_data["score_band"]
        analysis.decision_basis = analysis_data["decision_basis"]
        analysis.independent_rejection = analysis_data["independent_rejection"]
        analysis.policy_version = analysis_data["policy_version"]
        analysis.two_factor_deadline = analysis_data["two_factor_deadline"]
        analysis.reasoning = analysis_data["reasoning"]
        analysis.request_id = analysis_data["request_id"]

        self.session.add(analysis)
        return analysis

    def set_response(self, analysis: Analysis, response: dict) -> None:
        analysis.response = response

    def get_status(self, enumerator: str) -> AnalysisStatus:
        return self.session.query(AnalysisStatus).filter(AnalysisStatus.enumerator == enumerator).one()

    def create_two_factor_event(self, analysis: Analysis, result: str, recorded_at, request_id: str) -> TwoFactorEvent:
        two_factor_event = TwoFactorEvent()
        two_factor_event.result = result
        two_factor_event.recorded_at = recorded_at
        two_factor_event.request_id = request_id

        analysis.two_factor_event = two_factor_event
        self.session.add(two_factor_event)
        return two_factor_event
