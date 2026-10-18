from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func
from models.base import Base


class AnalysisStatus(Base):
    """A lista fechada das decisões. As três linhas nascem com o banco."""

    __tablename__ = "analysis_status"

    id = Column(Integer, primary_key=True)
    enumerator = Column(String(50), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("enumerator"),)

    APPROVED = "approved"
    TWO_FACTOR_REQUIRED = "two_factor_required"
    REJECTED = "rejected"
