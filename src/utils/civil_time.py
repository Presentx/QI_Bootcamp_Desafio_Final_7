from datetime import date, datetime, time, timedelta, timezone
from typing import Tuple
from zoneinfo import ZoneInfo


# A referência de horário civil da política [SIM]. O offset que veio no
# payload não diz o fuso do usuário, então ele só serve para achar o
# instante; noite, dia civil e fim de semana se medem sempre aqui.
CIVIL_TIMEZONE = ZoneInfo("America/Sao_Paulo")

NIGHT_START = time(20, 0)
NIGHT_END = time(6, 0)
BUSINESS_HOURS_START = time(7, 0)


def to_civil(instant: datetime) -> datetime:
    return instant.astimezone(CIVIL_TIMEZONE)


def civil_day(instant: datetime) -> date:
    return to_civil(instant).date()


def _civil_to_utc(day: date, hour: time) -> datetime:
    return datetime.combine(day, hour, tzinfo=CIVIL_TIMEZONE).astimezone(timezone.utc)


def civil_day_bounds(instant: datetime) -> Tuple[datetime, datetime]:
    """O dia civil que contém o instante, como [início, fim) em UTC."""
    day = civil_day(instant)
    return _civil_to_utc(day, time(0, 0)), _civil_to_utc(day + timedelta(days=1), time(0, 0))


def is_night(instant: datetime) -> bool:
    """Noite = [20:00, 06:00) no horário civil."""
    local_time = to_civil(instant).time()
    return local_time >= NIGHT_START or local_time < NIGHT_END


def night_bounds(instant: datetime) -> Tuple[datetime, datetime]:
    """A noite que contém o instante, como [início, fim) em UTC.

    Às 21:00 a noite começou hoje e termina amanhã às 06:00; às 01:00 ela
    começou ontem às 20:00. Só faz sentido para um instante noturno.
    """
    local = to_civil(instant)

    if local.time() >= NIGHT_START:
        start_day = local.date()
    else:
        start_day = local.date() - timedelta(days=1)

    return _civil_to_utc(start_day, NIGHT_START), _civil_to_utc(start_day + timedelta(days=1), NIGHT_END)


def is_weekend(instant: datetime) -> bool:
    return to_civil(instant).weekday() >= 5


def is_outside_business_hours(instant: datetime) -> bool:
    """O "fora do expediente" de S4: [20:00, 07:00) ou sábado e domingo."""
    local_time = to_civil(instant).time()
    return local_time >= NIGHT_START or local_time < BUSINESS_HOURS_START or is_weekend(instant)
