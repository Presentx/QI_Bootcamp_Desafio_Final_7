from sqlalchemy import text

from database import Context


class LockRepository:
    """As travas da análise: pg_advisory_xact_lock, liberadas no COMMIT.

    Advisory lock é uma trava sobre um NÚMERO, não sobre uma linha — e é
    isso que a política precisa. A trava do pagador protege limites
    acumulados (F6, F9, F11...), e não existe linha nenhuma que represente
    "o pagador" para um SELECT ... FOR UPDATE travar: o primeiro Pix de
    alguém ainda não tem linha alguma no banco. O número sai do texto
    ("id:pix-1", "pagador:123...") pelo hashtextextended do Postgres.

    O `_xact_` no nome é a metade importante: a trava acaba sozinha no
    COMMIT ou no ROLLBACK. Não existe caminho em que alguém esqueça de
    soltar.
    """

    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def set_lock_timeout(self, timeout: str) -> None:
        """Quanto esperar por uma trava nesta transação (o `true` é o SET LOCAL)."""
        self.session.execute(text("SELECT set_config('lock_timeout', :timeout, true)"), {"timeout": timeout})

    def acquire(self, key: str) -> None:
        self.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
