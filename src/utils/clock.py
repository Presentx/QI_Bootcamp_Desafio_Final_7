from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional


# O relógio do servidor, num lugar só. Toda leitura de "agora" do projeto
# passa por aqui: o received_at de uma análise, o prazo do 2FA, o estado
# de 2FA que o GET mostra.
#
# O ContextVar existe para o relógio de teste (src/middlewares/test_clock.py):
# quando ele está ligado e a requisição traz o cabeçalho, o "agora" desta
# requisição — e só desta — é o instante do cabeçalho.
_fixed_now: ContextVar[Optional[datetime]] = ContextVar("fixed_now", default=None)


def now() -> datetime:
    fixed = _fixed_now.get()

    if fixed is not None:
        return fixed

    return datetime.now(timezone.utc)


def set_fixed_now(instant: Optional[datetime]) -> None:
    _fixed_now.set(instant)


def iso(instant: Optional[datetime]) -> Optional[str]:
    """Um instante como texto UTC com milissegundos — o formato de tudo que sai."""
    if instant is None:
        return None

    return instant.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_instant(raw: str) -> Optional[datetime]:
    """Um ISO 8601 COM offset virado instante UTC; None se não for isso.

    O offset é obrigatório: sem ele, "14:00" não diz de que fuso é, e a
    política só trabalha com instantes absolutos. A precisão fica em
    milissegundos, como a política guarda.
    """
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        return None

    parsed = parsed.astimezone(timezone.utc)

    return parsed.replace(microsecond=(parsed.microsecond // 1000) * 1000)
