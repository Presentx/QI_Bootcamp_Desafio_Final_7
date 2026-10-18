from datetime import datetime, timedelta, timezone

# As datas da matriz de testes da política são em −03:00 (17/10/2026 é sábado).
BRT = timezone(timedelta(hours=-3))


def instant(local: str) -> datetime:
    """'2026-10-17 14:00' (ou com :ss e .mmm) em horário de Brasília, como datetime."""
    for pattern in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(local, pattern).replace(tzinfo=BRT)
        except ValueError:
            continue

    raise ValueError(f"data de teste fora do formato: {local}")


def to_iso(moment: datetime) -> str:
    """O instante no formato do contrato, com milissegundos e offset."""
    return moment.isoformat(timespec="milliseconds")


def at(local: str) -> str:
    return to_iso(instant(local))


def shift(local: str, **delta) -> str:
    """O mesmo instante deslocado: shift('2026-10-17 14:00', seconds=1)."""
    return to_iso(instant(local) + timedelta(**delta))


def days(start: str, count: int, step_days: int = 1) -> list:
    """'count' datas a partir de 'start', de 'step_days' em 'step_days' dias, no mesmo horário."""
    first = instant(start)
    moments = []
    for index in range(count):
        moment = first + timedelta(days=index * step_days)
        moments.append(moment.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])

    return moments
