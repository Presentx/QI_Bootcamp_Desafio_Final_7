from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import relationship
from models.base import Base
from models import Analysis


class TwoFactorEvent(Base):
    """O resultado do 2FA de uma análise (rota E1). PE → AU | NC, uma vez só.

    O UNIQUE(analysis_id) é o que torna a transição única: AU e NC são
    finais. A expiração (PE sem resposta até o prazo) não grava linha —
    ela é derivada do `two_factor_deadline` da análise, no corte de quem lê.
    """

    __tablename__ = "two_factor_event"

    id = Column(Integer, primary_key=True)
    analysis_id = Column(Integer, ForeignKey(Analysis.id), nullable=False)
    result = Column(String(10), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False)
    request_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("analysis_id"),)

    analysis = relationship("Analysis", foreign_keys=[analysis_id], back_populates="two_factor_event")

    CONFIRMED = "confirmed"
    DECLINED = "declined"
    CANCELLED = "cancelled"
