"""Os pisos de 2FA (seção 5.1).

Cada piso, sozinho, eleva a decisão para no mínimo two_factor_required.
Pisos não somam pontos e não encerram a análise: todos rodam, e todos
entram na fundamentação, disparados ou não.

Cada função devolve o registro da regra: `fired`, o valor observado e o
limiar — o bastante para alguém refazer a conta sem abrir o código.
"""
from controllers.policy import parameters
from controllers.policy.facts import DEVICE_TRUSTED, Facts
from controllers.policy.history import iso, max_window, median
from utils.civil_time import civil_day, civil_day_bounds, is_night, night_bounds


def _not_applicable(code: str, reason: str) -> dict:
    return {"code": code, "fired": False, "applicable": False, "reason": reason}


def f1_missing_geolocation(facts: Facts) -> dict:
    current = facts.current
    if current.payer_type != "PF":
        return _not_applicable("F1", "payer is PJ")

    recent = []
    for event in facts.payer_events():
        if facts.t - parameters.F1_WINDOW < event.instant < facts.t:
            recent.append(event)

    all_located = len(recent) >= parameters.F1_MIN_EVENTS
    for event in recent:
        if not event.has_geo:
            all_located = False

    fired = all_located and not current.has_geo and current.amount > parameters.F1_MIN_AMOUNT

    return {
        "code": "F1",
        "fired": fired,
        "events_30d": len(recent),
        "all_events_with_geolocation": all_located,
        "current_has_geolocation": current.has_geo,
        "observed": current.amount,
        "threshold": parameters.F1_MIN_AMOUNT,
        "event_ids": facts.reference(recent),
    }


def f2_warming_up(facts: Facts) -> dict:
    current = facts.current
    if current.payer_type != "PF":
        return _not_applicable("F2", "payer is PJ")

    fired = (
        not facts.is_mature
        and not facts.is_known_destination
        and current.amount > parameters.F2_MIN_AMOUNT
    )

    return {
        "code": "F2",
        "fired": fired,
        "mature": facts.is_mature,
        "known_destination": facts.is_known_destination,
        "observed": current.amount,
        "threshold": parameters.F2_MIN_AMOUNT,
    }


def f3_new_pf_accounts(facts: Facts) -> dict:
    current = facts.current
    is_pf_to_pf = current.payer_type == "PF" and current.destination_type == "PF"
    different_owners = current.payer_document != current.destination_document

    fired = (
        is_pf_to_pf
        and different_owners
        and facts.source_account_age < parameters.NEW_ACCOUNT_DAYS
        and facts.destination_account_age < parameters.NEW_ACCOUNT_DAYS
        and current.amount > parameters.F3_MIN_AMOUNT
    )

    return {
        "code": "F3",
        "fired": fired,
        "pf_to_pf_different_owners": is_pf_to_pf and different_owners,
        "source_account_age_days": facts.source_account_age,
        "destination_account_age_days": facts.destination_account_age,
        "observed": current.amount,
        "threshold": parameters.F3_MIN_AMOUNT,
    }


def f4_pj_paying_new_cpf(facts: Facts) -> dict:
    current = facts.current
    if current.payer_type != "PJ" or current.destination_type != "PF":
        return _not_applicable("F4", "not PJ to PF")

    evidences = []
    for event in facts.history.recipient_events:
        if event.is_good_evidence(facts.t):
            evidences.append(event)

    fired = not evidences and current.amount > parameters.F4_MIN_AMOUNT

    return {
        "code": "F4",
        "fired": fired,
        "good_evidences_to_recipient": len(evidences),
        "observed": current.amount,
        "threshold": parameters.F4_MIN_AMOUNT,
        "event_ids": facts.reference(evidences),
    }


def f5_pj_new_account(facts: Facts) -> dict:
    current = facts.current
    if current.payer_type != "PJ":
        return _not_applicable("F5", "payer is PF")

    fired = facts.source_account_age < parameters.NEW_ACCOUNT_DAYS and current.amount > parameters.F5_MIN_AMOUNT

    return {
        "code": "F5",
        "fired": fired,
        "source_account_age_days": facts.source_account_age,
        "observed": current.amount,
        "threshold": parameters.F5_MIN_AMOUNT,
    }


def _night_exposure(facts: Facts):
    """A exposição do pagador na noite inteira que contém t, inclusive depois de t."""
    night_start, night_end = night_bounds(facts.t)

    members = []
    for event in facts.exposure_events():
        if night_start <= event.instant < night_end:
            members.append(event)

    total = facts.current.amount
    for event in members:
        total = total + event.amount

    window = {"start": iso(night_start), "end": iso(night_end), "event_ids": facts.reference(members)}

    return total, window


def f6_night_pf_to_pf(facts: Facts) -> dict:
    current = facts.current
    is_pf_to_pf = current.payer_type == "PF" and current.destination_type == "PF"
    different_owners = current.payer_document != current.destination_document

    if not (is_pf_to_pf and different_owners):
        return _not_applicable("F6", "not PF to PF between different owners")

    if not is_night(facts.t):
        return {"code": "F6", "fired": False, "night": False}

    total, window = _night_exposure(facts)

    return {
        "code": "F6",
        "fired": total > parameters.F6_NIGHT_LIMIT,
        "night": True,
        "window": window,
        "observed": total,
        "threshold": parameters.F6_NIGHT_LIMIT,
    }


def f7_night_pj(facts: Facts) -> dict:
    current = facts.current
    if current.payer_type != "PJ":
        return _not_applicable("F7", "payer is PF")

    if not is_night(facts.t):
        return {"code": "F7", "fired": False, "night": False}

    total, window = _night_exposure(facts)
    night_start, _night_end = night_bounds(facts.t)

    # A referência: evidências boas noturnas em [t − 90 d, início da noite atual).
    nightly_totals = {}
    nightly_events = []
    for event in facts.good_evidences_between(facts.t - parameters.F7_REFERENCE_WINDOW, night_start):
        if is_night(event.instant):
            night_key = night_bounds(event.instant)[0]
            nightly_totals[night_key] = nightly_totals.get(night_key, 0) + event.amount
            nightly_events.append(event)

    oldest = None
    for event in nightly_events:
        if oldest is None or event.instant < oldest:
            oldest = event.instant

    is_mature = (
        len(nightly_events) >= parameters.F7_MATURITY_MIN_EVENTS
        and len(nightly_totals) >= parameters.F7_MATURITY_MIN_NIGHTS
        and oldest is not None
        and oldest <= facts.t - parameters.F7_MATURITY_MIN_AGE
    )

    reference_median = None
    limit = parameters.F7_BASE_LIMIT
    if is_mature:
        reference_median = median(list(nightly_totals.values()))
        limit = max(parameters.F7_BASE_LIMIT, min(parameters.F7_MAX_LIMIT, 2 * reference_median))

    nightly_report = []
    for night_key in sorted(nightly_totals):
        nightly_report.append({"night_start": iso(night_key), "total": nightly_totals[night_key]})

    return {
        "code": "F7",
        "fired": total > limit,
        "night": True,
        "window": window,
        "nightly_totals": nightly_report,
        "maturity": {"nocturnal_evidences": len(nightly_events), "nights": len(nightly_totals), "mature": is_mature},
        "R": reference_median,
        "observed": total,
        "threshold": limit,
    }


def f8_untrusted_device(facts: Facts) -> dict:
    current = facts.current
    if current.payer_type != "PF":
        return _not_applicable("F8", "payer is PJ")

    if facts.device_grade == DEVICE_TRUSTED:
        return {"code": "F8", "fired": False, "device_grade": facts.device_grade}

    day_start, day_end = civil_day_bounds(facts.t)
    members = []
    for event in facts.exposure_events():
        if event.device_id == current.device_id and day_start <= event.instant < day_end:
            members.append(event)

    total = current.amount
    for event in members:
        total = total + event.amount

    return {
        "code": "F8",
        "fired": total > parameters.F8_DAY_LIMIT,
        "device_grade": facts.device_grade,
        "window": {"start": iso(day_start), "end": iso(day_end), "event_ids": facts.reference(members)},
        "observed": total,
        "threshold": parameters.F8_DAY_LIMIT,
    }


def f9_ceiling_in_60_minutes(facts: Facts) -> dict:
    exposure = facts.exposure_events()
    window = max_window(exposure, facts.t, parameters.F9_WINDOW, facts.current.amount, lambda event: event.amount)

    for event in exposure:
        if event.transaction_id in window["event_ids"]:
            facts.reference([event])

    return {
        "code": "F9",
        "fired": window["value"] > facts.ceiling,
        "window": window,
        "observed": window["value"],
        "threshold": facts.ceiling,
    }


def f10_test_then_high_amount(facts: Facts) -> dict:
    current = facts.current
    if facts.destination_account_age >= parameters.NEW_ACCOUNT_DAYS:
        return {"code": "F10", "fired": False, "destination_account_age_days": facts.destination_account_age}

    tests = []
    for event in facts.history.relationship_events:
        is_test = (
            event.is_exposure
            and event.amount <= parameters.F10_MAX_TEST_AMOUNT
            and facts.t - parameters.F10_TEST_WINDOW < event.instant <= facts.t
        )
        if is_test:
            tests.append(event)

    matched_test = None
    for test in tests:
        relation_before_test = False
        for event in facts.history.relationship_events:
            if event.is_relation_evidence(facts.t) and event.instant < test.instant:
                relation_before_test = True

        large_enough = (
            current.amount >= parameters.F10_MIN_AMOUNT
            and current.amount >= parameters.F10_TEST_MULTIPLIER * test.amount
        )

        if not relation_before_test and large_enough:
            matched_test = test
            break

    result = {
        "code": "F10",
        "fired": matched_test is not None,
        "destination_account_age_days": facts.destination_account_age,
        "tests_in_window": facts.reference(tests),
        "observed": current.amount,
        "threshold": parameters.F10_MIN_AMOUNT,
    }

    if matched_test is not None:
        result["matched_test"] = {"event_id": matched_test.transaction_id, "amount": matched_test.amount}

    return result


def f11_onboarding_ramp(facts: Facts) -> dict:
    current = facts.current
    if not facts.is_new_person:
        return {"code": "F11", "fired": False, "new_person": False}

    good_days = set()
    downgrades = []
    for event in facts.payer_events():
        if event.is_good_evidence(facts.t) and event.amount >= parameters.ACTIVE_DAY_MIN_AMOUNT:
            good_days.add(civil_day(event.instant))

        if event.instant < facts.t and (event.is_independent_rejection() or event.state == "NC"):
            downgrades.append(event)

    level = 0
    for index, (minimum_days, _pf_limit, _pj_limit) in enumerate(parameters.F11_LEVELS):
        if len(good_days) >= minimum_days:
            level = index

    level_before_downgrades = level
    level = max(0, level - len(downgrades))

    day_start, day_end = civil_day_bounds(facts.t)
    requests_today = 1
    day_exposure = []
    for event in facts.payer_events():
        if day_start <= event.instant < day_end:
            requests_today = requests_today + 1
            if event.is_exposure:
                day_exposure.append(event)

    if current.payer_type == "PF":
        max_requests = parameters.F11_MAX_DAILY_REQUESTS_PF
    else:
        max_requests = parameters.F11_MAX_DAILY_REQUESTS_PJ

    too_many_requests = requests_today > max_requests
    if too_many_requests:
        level = 0

    _minimum_days, pf_limit, pj_limit = parameters.F11_LEVELS[level]
    if current.payer_type == "PF":
        limit = pf_limit
    else:
        limit = pj_limit

    limit = min(limit, facts.ceiling)

    total = current.amount
    for event in day_exposure:
        total = total + event.amount

    return {
        "code": "F11",
        "fired": total > limit,
        "new_person": True,
        "good_days": len(good_days),
        "level_from_good_days": level_before_downgrades,
        "downgrades": facts.reference(downgrades),
        "requests_today": requests_today,
        "too_many_requests": too_many_requests,
        "level": level,
        "window": {"start": iso(day_start), "end": iso(day_end), "event_ids": facts.reference(day_exposure)},
        "observed": total,
        "threshold": limit,
    }


FLOORS = [
    f1_missing_geolocation,
    f2_warming_up,
    f3_new_pf_accounts,
    f4_pj_paying_new_cpf,
    f5_pj_new_account,
    f6_night_pf_to_pf,
    f7_night_pj,
    f8_untrusted_device,
    f9_ceiling_in_60_minutes,
    f10_test_then_high_amount,
    f11_onboarding_ramp,
]
