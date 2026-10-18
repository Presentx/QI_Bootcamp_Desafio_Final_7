"""Os atenuantes (seção 5.3): o histórico bom pesando a favor do cliente.

Somados, nunca passam de 30 nem de metade dos pontos (D* em decision.py):
um bom histórico suaviza o score, mas não apaga sozinho um sinal forte.
"""
from controllers.policy import parameters
from controllers.policy.facts import DEVICE_TRUSTED, Facts
from controllers.policy.history import iso, median
from utils.civil_time import civil_day


def a1_recurring_destination(facts: Facts) -> dict:
    evidences = facts.relation_evidences

    days = set()
    oldest = None
    for event in evidences:
        days.add(civil_day(event.instant))
        if oldest is None or event.instant < oldest:
            oldest = event.instant

    is_recurring = (
        len(evidences) >= parameters.A1_MIN_EVIDENCES
        and len(days) >= parameters.A1_MIN_DAYS
        and oldest <= facts.t - parameters.A1_MIN_AGE
    )

    if not is_recurring:
        return {
            "code": "A1",
            "applied": False,
            "discount": 0,
            "relation_evidences": len(evidences),
            "civil_days": len(days),
            "first_evidence": iso(oldest),
        }

    last_evidences = evidences[: parameters.A1_LAST_EVIDENCES]
    reference_median = median([event.amount for event in last_evidences])

    # 2 × valor ≤ 3 × M: o pagamento cabe em até 1,5 vez o habitual.
    applied = 2 * facts.current.amount <= 3 * reference_median

    discount = 0
    if applied:
        discount = parameters.A1_DISCOUNT

    return {
        "code": "A1",
        "applied": applied,
        "discount": discount,
        "relation_evidences": len(evidences),
        "civil_days": len(days),
        "first_evidence": iso(oldest),
        "last_12": facts.reference(last_evidences),
        "median": reference_median,
    }


def a2_same_owner(facts: Facts) -> dict:
    current = facts.current
    applied = (
        current.payer_document == current.destination_document
        and facts.destination_account_age >= parameters.A2_MIN_DESTINATION_AGE_DAYS
    )

    discount = 0
    if applied:
        discount = parameters.A2_DISCOUNT

    return {
        "code": "A2",
        "applied": applied,
        "discount": discount,
        "same_owner": current.payer_document == current.destination_document,
        "destination_account_age_days": facts.destination_account_age,
    }


def a3_trusted_context(facts: Facts) -> dict:
    current = facts.current
    device_trusted = facts.device_grade == DEVICE_TRUSTED

    nearest = facts.distance_to_nearest_habitual()
    near_habitual = nearest is not None and nearest <= parameters.HABITUAL_LOCATION_RADIUS_KM

    # Sem geolocalização agora só conta como contexto confiável se o
    # pagador também não costuma mandar — senão a ausência é o sinal (F1).
    recent_geo = False
    for event in facts.payer_events():
        if event.has_geo and facts.t - parameters.A3_NO_GEO_WINDOW < event.instant < facts.t:
            recent_geo = True

    never_sends_geo = not current.has_geo and not recent_geo

    applied = device_trusted and (near_habitual or never_sends_geo)

    discount = 0
    if applied:
        discount = parameters.A3_DISCOUNT

    nearest_report = None
    if nearest is not None:
        nearest_report = round(nearest, 3)

    return {
        "code": "A3",
        "applied": applied,
        "discount": discount,
        "device_trusted": device_trusted,
        "nearest_habitual_km": nearest_report,
        "no_geolocation_context": never_sends_geo,
    }


def a4_continuous_trust(facts: Facts) -> dict:
    window_start = facts.t - parameters.A4_WINDOW_START
    window_end = facts.t - parameters.A4_WINDOW_END

    active_days = set()
    for event in facts.payer_events():
        is_active = (
            event.is_good_evidence(facts.t)
            and event.amount >= parameters.ACTIVE_DAY_MIN_AMOUNT
            and window_start < event.instant <= window_end
        )
        if is_active:
            active_days.add(civil_day(event.instant))

    full_discount = min(parameters.A4_MAX_DISCOUNT, len(active_days) // 2)

    recent_rejections = []
    for event in facts.payer_events():
        if event.is_independent_rejection() and facts.t - parameters.A4_REJECTION_WINDOW < event.instant < facts.t:
            recent_rejections.append(event)

    discount = full_discount
    if recent_rejections:
        discount = full_discount // 2

    return {
        "code": "A4",
        "applied": discount > 0,
        "discount": discount,
        "full_discount": full_discount,
        "active_days": len(active_days),
        "halved_by": facts.reference(recent_rejections),
    }


ATTENUATORS = [
    a1_recurring_destination,
    a2_same_owner,
    a3_trusted_context,
    a4_continuous_trust,
]
