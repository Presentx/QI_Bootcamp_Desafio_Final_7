from sqlalchemy import Boolean, CHAR, Column, DateTime, ForeignKey, Integer, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from models.base import Base
from models import AnalysisStatus, PixTransaction


class Analysis(Base):
    """A decisão sobre um Pix — imutável depois do COMMIT.

    `response` é a resposta congelada: é ela que o POST repetido e o GET
    devolvem, sem recalcular nada. `reasoning` é a fundamentação (seção
    7.1 da política), interna. `independent_rejection` alimenta S12, a
    redução de A4 e o rebaixamento da rampa F11 nas análises seguintes.
    """

    __tablename__ = "analysis"

    id = Column(Integer, primary_key=True)
    analysis_key = Column(CHAR(36), nullable=False)
    pix_transaction_id = Column(Integer, ForeignKey(PixTransaction.id), nullable=False)
    status_id = Column(Integer, ForeignKey(AnalysisStatus.id), nullable=False)
    score = Column(SmallInteger, nullable=False)
    score_band = Column(String(10), nullable=False)
    decision_basis = Column(String(10), nullable=False)
    independent_rejection = Column(Boolean, nullable=False)
    policy_version = Column(String(10), nullable=False)
    two_factor_deadline = Column(DateTime(timezone=True), nullable=True)
    response = Column(JSONB, nullable=False)
    reasoning = Column(JSONB, nullable=False)
    request_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("analysis_key"),
        UniqueConstraint("pix_transaction_id"),
    )

    status = relationship("AnalysisStatus", foreign_keys=[status_id], lazy="joined")
    pix_transaction = relationship("PixTransaction", foreign_keys=[pix_transaction_id], back_populates="analysis")
    two_factor_event = relationship("TwoFactorEvent", back_populates="analysis", uselist=False, lazy="joined")
