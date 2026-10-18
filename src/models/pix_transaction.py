from sqlalchemy import BigInteger, CHAR, Column, DateTime, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from models.base import Base


class PixTransaction(Base):
    """O Pix como chegou, normalizado: documentos só com dígitos, instantes em UTC.

    É o histórico que as regras consultam. Cada linha tem exatamente uma
    análise (a tabela `analysis`), gravada no mesmo COMMIT.

    Duas datas, dois relógios: `event_instant` é o instante de
    `transaction_date` (relógio de evento, usado nas janelas) e
    `received_at` é o relógio do servidor (usado no corte do histórico).
    """

    __tablename__ = "pix_transaction"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(String(64), nullable=False)
    amount = Column(BigInteger, nullable=False)
    event_instant = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    payer_document = Column(String(14), nullable=False)
    payer_type = Column(CHAR(2), nullable=False)
    source_branch = Column(String(20), nullable=False)
    source_account = Column(String(20), nullable=False)
    source_opening_date = Column(DateTime(timezone=True), nullable=False)
    destination_participant = Column(CHAR(8), nullable=False)
    destination_branch = Column(String(20), nullable=False)
    destination_account = Column(String(20), nullable=False)
    destination_opening_date = Column(DateTime(timezone=True), nullable=False)
    destination_document = Column(String(14), nullable=False)
    destination_type = Column(CHAR(2), nullable=False)
    device_id = Column(String(128), nullable=False)
    channel = Column(String(20), nullable=False)
    ip = Column(String(45), nullable=False)
    latitude = Column(Numeric(9, 6), nullable=True)
    longitude = Column(Numeric(9, 6), nullable=True)
    payload = Column(JSONB, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("transaction_id"),)

    # `joined`: toda consulta de histórico precisa da decisão de cada
    # evento, e trazê-la na mesma consulta evita uma ida ao banco por linha.
    analysis = relationship("Analysis", back_populates="pix_transaction", uselist=False, lazy="joined")
