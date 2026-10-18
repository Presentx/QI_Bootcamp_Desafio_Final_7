"""As regras de score (seção 5.2).

Cada regra pertence a um grupo; dentro do grupo os pontos somam até o
teto (o teto é aplicado em decision.py). Em regra de dois níveis, vale
o maior. As supressões (S3 por A1, S11 por S4) são aplicadas pelo
engine, que é quem enxerga as duas regras ao mesmo tempo — aqui cada
regra só diz se a sua condição bateu.
"""
from controllers.policy import parameters
from controllers.policy.facts import DEVICE_NEW, DEVICE_OBSERVED, Facts
from controllers.policy.history import default_order, iso, max_window, median
from utils.civil_time import is_night, is_outside_business_hours, is_weekend
from utils.geo import haversine_km


def _rule(code: str, group: str, points: int, **details) -> dict:
    result = {"code": code, "group": group, "fired": points > 0, "points": points}
    result.update(details)
    return result


def s1_above_ceiling(facts: Facts) -> dict:
    points = 0
    if facts.current.amount > facts.ceiling:
        points = parameters.S1_POINTS

    return _rule("S1", parameters.GROUP_VALUE, points, observed=facts.current.amount, threshold=facts.ceiling)


def s2_exposure_24h(facts: Facts) -> dict:
    exposure = facts.exposure_events()
    window = max_window(exposure, facts.t, parameters.S2_WINDOW, facts.current.amount, lambda event: event.amount)
    threshold = parameters.S2_CEILING_MULTIPLIER * facts.ceiling

    points = 0
    if window["value"] > threshold:
        points = parameters.S2_POINTS

    for event in exposure:
        if event.transaction_id in window["event_ids"]:
            facts.reference([event])

    return _rule("S2", parameters.GROUP_VALUE, points, window=window, observed=window["value"], threshold=threshold)


def _value_references(facts: Facts, destination_type: str = None) -> list:
    """Até 30 evidências boas em [t − 90 d, t), na ordenação padrão."""
    evidences = []
    for event in facts.good_evidences_between(facts.t - parameters.S3_WINDOW, facts.t):
        if destination_type is None or event.destination_type == destination_type:
            evidences.append(event)

    return default_order(evidences)[: parameters.S3_MAX_REFERENCES]


def s3_median_deviation(facts: Facts) -> dict:
    current = facts.current

    # PJ é comparado só com pagamentos ao mesmo tipo de recebedor: a folha
    # (PJ→PF) e o fornecedor (PJ→PJ) vivem em escalas diferentes.
    destination_type = None
    if current.payer_type == "PJ":
        destination_type = current.destination_type

    references = _value_references(facts, destination_type)
    reference_ids = facts.reference(references)

    if len(references) < parameters.S3_MIN_REFERENCES:
        return _rule("S3", parameters.GROUP_VALUE, 0, references=reference_ids, reason="fewer than 5 references")

    reference_median = median([event.amount for event in references])

    points = 0
    if current.amount >= parameters.S3_MIN_AMOUNT:
        if current.amount > parameters.S3_HIGH_MULTIPLIER * reference_median:
            points = parameters.S3_HIGH_POINTS
        elif current.amount > parameters.S3_LOW_MULTIPLIER * reference_median:
            points = parameters.S3_LOW_POINTS

    return _rule(
        "S3",
        parameters.GROUP_VALUE,
        points,
        references=reference_ids,
        reference_amounts=[event.amount for event in references],
        median=reference_median,
        observed=current.amount,
        threshold={"x5": parameters.S3_LOW_MULTIPLIER * reference_median, "x10": parameters.S3_HIGH_MULTIPLIER * reference_median},
    )


def s4_pj_to_pf_after_hours(facts: Facts) -> dict:
    current = facts.current
    if current.payer_type != "PJ" or current.destination_type != "PF":
        return _rule("S4", parameters.GROUP_VALUE, 0, applicable=False)

    if not is_outside_business_hours(facts.t):
        return _rule("S4", parameters.GROUP_VALUE, 0, outside_business_hours=False)

    references = _value_references(facts, "PF")
    reference_ids = facts.reference(references)

    if len(references) >= parameters.S3_MIN_REFERENCES:
        reference_median = median([event.amount for event in references])
        threshold = parameters.S4_MULTIPLIER * reference_median
    else:
        reference_median = None
        threshold = parameters.S4_FALLBACK_AMOUNT

    points = 0
    if current.amount > threshold:
        points = parameters.S4_POINTS

    return _rule(
        "S4",
        parameters.GROUP_VALUE,
        points,
        outside_business_hours=True,
        references=reference_ids,
        median=reference_median,
        observed=current.amount,
        threshold=threshold,
    )


def s5_device(facts: Facts) -> dict:
    points = 0
    if facts.current.amount > parameters.S5_MIN_AMOUNT:
        if facts.device_grade == DEVICE_NEW:
            points = parameters.S5_NEW_DEVICE_POINTS
        elif facts.device_grade == DEVICE_OBSERVED:
            points = parameters.S5_OBSERVED_DEVICE_POINTS

    return _rule(
        "S5",
        parameters.GROUP_DEVICE,
        points,
        device_grade=facts.device_grade,
        observed=facts.current.amount,
        threshold=parameters.S5_MIN_AMOUNT,
    )


def s6_attempts(facts: Facts) -> dict:
    """Tentativas em QUALQUER estado: uma rejeição também é uma tentativa."""
    short_limit, long_limit = parameters.S6_THRESHOLDS[facts.current.payer_type]
    events = facts.payer_events()

    short_window = max_window(events, facts.t, parameters.S6_SHORT_WINDOW, 1, lambda event: 1)
    long_window = max_window(events, facts.t, parameters.S6_LONG_WINDOW, 1, lambda event: 1)

    points = 0
    if short_window["value"] >= short_limit or long_window["value"] >= long_limit:
        points = parameters.S6_POINTS

    for event in events:
        if event.transaction_id in short_window["event_ids"] or event.transaction_id in long_window["event_ids"]:
            facts.reference([event])

    return _rule(
        "S6",
        parameters.GROUP_VELOCITY,
        points,
        window_10_min=short_window,
        window_60_min=long_window,
        threshold={"10_min": short_limit, "60_min": long_limit},
    )


def s7_new_destination_account(facts: Facts) -> dict:
    current = facts.current
    is_pf_to_pf = current.payer_type == "PF" and current.destination_type == "PF"
    age = facts.destination_account_age

    points = 0
    if age < parameters.S7_VERY_NEW_DAYS:
        if is_pf_to_pf:
            points = parameters.S7_VERY_NEW_PF_TO_PF_POINTS
        else:
            points = parameters.S7_VERY_NEW_POINTS
    elif age < parameters.NEW_ACCOUNT_DAYS:
        if is_pf_to_pf:
            points = parameters.S7_NEW_PF_TO_PF_POINTS
        else:
            points = parameters.S7_NEW_POINTS

    return _rule("S7", parameters.GROUP_COUNTERPARTY, points, destination_account_age_days=age, pf_to_pf=is_pf_to_pf)


def s8_new_payers_to_new_pf_account(facts: Facts) -> dict:
    current = facts.current
    if current.destination_type != "PF" or facts.destination_account_age >= parameters.NEW_ACCOUNT_DAYS:
        return _rule("S8", parameters.GROUP_COUNTERPARTY, 0, applicable=False)

    counted_payers = set(facts.history.destination_new_payers.keys())

    # O pagador atual entra provisoriamente se ainda não tem evento não
    # rejeitado anterior para a conta — contado independentemente da
    # decisão atual, para que a análise não dependa do próprio resultado.
    has_previous = False
    for event in facts.history.relationship_events:
        if event.state != "RJ" and event.instant < facts.t:
            has_previous = True

    current_included_provisionally = False
    if current.payer_document not in counted_payers and not has_previous:
        counted_payers.add(current.payer_document)
        current_included_provisionally = True

    points = 0
    if len(counted_payers) >= parameters.S8_MIN_PAYERS:
        points = parameters.S8_POINTS

    return _rule(
        "S8",
        parameters.GROUP_COUNTERPARTY,
        points,
        counted_payers=len(counted_payers),
        current_included_provisionally=current_included_provisionally,
        threshold=parameters.S8_MIN_PAYERS,
    )


def s9_outside_habitual_locations(facts: Facts) -> dict:
    if not facts.current.has_geo:
        return _rule("S9", parameters.GROUP_LOCATION, 0, reason="current transaction has no geolocation")

    nearest = facts.distance_to_nearest_habitual()
    if nearest is None:
        return _rule("S9", parameters.GROUP_LOCATION, 0, reason="no habitual location")

    points = 0
    if nearest > parameters.S9_DISTANCE_KM:
        points = parameters.S9_POINTS

    return _rule(
        "S9",
        parameters.GROUP_LOCATION,
        points,
        nearest_habitual_km=round(nearest, 3),
        threshold=parameters.S9_DISTANCE_KM,
    )


def s10_impossible_travel(facts: Facts) -> dict:
    current = facts.current
    if current.payer_type != "PF":
        return _rule("S10", parameters.GROUP_LOCATION, 0, applicable=False)

    previous = facts.history.previous_geo_event
    if not current.has_geo or previous is None:
        return _rule("S10", parameters.GROUP_LOCATION, 0, reason="no current or previous geolocation")

    facts.reference([previous])

    elapsed_hours = abs((facts.t - previous.instant).total_seconds()) / 3600
    if elapsed_hours == 0:
        return _rule("S10", parameters.GROUP_LOCATION, 0, previous_event_id=previous.transaction_id, reason="zero elapsed time")

    distance = haversine_km(previous.latitude, previous.longitude, current.latitude, current.longitude)
    speed = distance / elapsed_hours

    points = 0
    if distance >= parameters.S10_MIN_DISTANCE_KM and speed > parameters.S10_MAX_SPEED_KMH:
        points = parameters.S10_POINTS

    return _rule(
        "S10",
        parameters.GROUP_LOCATION,
        points,
        previous_event_id=previous.transaction_id,
        distance_km=round(distance, 3),
        speed_kmh=round(speed, 3),
        threshold={"distance_km": parameters.S10_MIN_DISTANCE_KM, "speed_kmh": parameters.S10_MAX_SPEED_KMH},
    )


def s11_unusual_time(facts: Facts) -> dict:
    current = facts.current

    if current.payer_type == "PF":
        def is_unusual(instant):
            return is_night(instant)
    else:
        def is_unusual(instant):
            return is_night(instant) or is_weekend(instant)

    if not is_unusual(facts.t):
        return _rule("S11", parameters.GROUP_TIME, 0, unusual_time=False)

    references = facts.good_evidences_between(facts.t - parameters.S11_WINDOW, facts.t)

    unusual_count = 0
    for event in references:
        if is_unusual(event.instant):
            unusual_count = unusual_count + 1

    share = None
    points = 0
    if len(references) >= parameters.S11_MIN_REFERENCES:
        share = unusual_count / len(references)
        if share < parameters.S11_MAX_SHARE:
            points = parameters.S11_POINTS

    return _rule(
        "S11",
        parameters.GROUP_TIME,
        points,
        unusual_time=True,
        references=len(references),
        unusual_references=unusual_count,
        share=share,
        threshold={"min_references": parameters.S11_MIN_REFERENCES, "max_share": parameters.S11_MAX_SHARE},
    )


def s12_insisting_after_rejection(facts: Facts) -> dict:
    """Só rejeições INDEPENDENTES contam — senão S12 se alimentaria de si mesma."""
    rejections = []
    for event in facts.payer_events():
        if event.is_independent_rejection() and facts.t - parameters.S12_WINDOW < event.instant < facts.t:
            rejections.append(event)

    points = 0
    if rejections:
        points = parameters.S12_POINTS

    return {
        "code": "S12",
        "group": "escalada",
        "fired": points > 0,
        "points": points,
        "independent_rejections": facts.reference(rejections),
        "window": {"start": iso(facts.t - parameters.S12_WINDOW), "end": iso(facts.t)},
    }


GROUPED_RULES = [
    s1_above_ceiling,
    s2_exposure_24h,
    s3_median_deviation,
    s4_pj_to_pf_after_hours,
    s5_device,
    s6_attempts,
    s7_new_destination_account,
    s8_new_payers_to_new_pf_account,
    s9_outside_habitual_locations,
    s10_impossible_travel,
    s11_unusual_time,
]
