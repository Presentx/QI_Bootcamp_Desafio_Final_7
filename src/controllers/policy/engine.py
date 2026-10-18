"""O motor: pisos, regras, atenuantes, fórmula, precedência — e a fundamentação.

Recebe a transação normalizada e o histórico já no estado do corte, e
devolve a decisão com a fundamentação inteira (seção 7.1). Não fala com
o banco e não sabe que existe HTTP: quem consulta é o controller, quem
responde é o resource.
"""
from controllers.policy.attenuators import ATTENUATORS
from controllers.policy.decision import REJECTED, calculate, precedence
from controllers.policy.facts import Facts
from controllers.policy.floors import FLOORS
from controllers.policy.history import CurrentTransaction, History, default_order, iso
from controllers.policy.score_rules import GROUPED_RULES, s12_insisting_after_rejection


def _suppress(rule: dict, by: str, suppressions: list) -> None:
    rule["points_before_suppression"] = rule["points"]
    rule["points"] = 0
    rule["suppressed_by"] = by
    suppressions.append({"rule": rule["code"], "by": by})


def evaluate(current: CurrentTransaction, history: History, policy_version: str, two_factor_route_enabled: bool) -> dict:
    facts = Facts(current, history)

    floors = []
    for floor in FLOORS:
        floors.append(floor(facts))

    fired_floors = []
    for floor in floors:
        if floor["fired"]:
            fired_floors.append(floor["code"])

    rules = []
    for rule in GROUPED_RULES:
        rules.append(rule(facts))

    escalation = s12_insisting_after_rejection(facts)

    attenuators = []
    for attenuator in ATTENUATORS:
        attenuators.append(attenuator(facts))

    rules_by_code = {}
    for rule in rules:
        rules_by_code[rule["code"]] = rule

    attenuators_by_code = {}
    for attenuator in attenuators:
        attenuators_by_code[attenuator["code"]] = attenuator

    # Dupla contagem: S3 e A1 olham o mesmo pagamento recorrente por
    # lados opostos; S11 e S4 punem o mesmo horário. Fica um de cada par.
    suppressions = []
    if attenuators_by_code["A1"]["applied"] and rules_by_code["S3"]["points"] > 0:
        _suppress(rules_by_code["S3"], "A1", suppressions)

    if rules_by_code["S4"]["points"] > 0 and rules_by_code["S11"]["points"] > 0:
        _suppress(rules_by_code["S11"], "S4", suppressions)

    discounts = {}
    for attenuator in attenuators:
        discounts[attenuator["code"]] = attenuator["discount"]

    calculation = calculate(rules, escalation["points"], discounts)
    status, decision_basis = precedence(calculation, fired_floors)

    # Rejeição independente: continua rejected sem S12 e com A4 sem a
    # redução pela metade. É ela, e só ela, que alimenta S12 e a redução
    # de A4 das próximas análises — sem isso, uma rejeição puxaria a
    # seguinte, que puxaria a seguinte, num ciclo de penalização.
    counterfactual = None
    independent_rejection = False
    if status == REJECTED:
        counterfactual_discounts = dict(discounts)
        counterfactual_discounts["A4"] = attenuators_by_code["A4"]["full_discount"]

        counterfactual_calculation = calculate(rules, 0, counterfactual_discounts)
        counterfactual_status, _basis = precedence(counterfactual_calculation, fired_floors)
        independent_rejection = counterfactual_status == REJECTED

        counterfactual = {
            "P": counterfactual_calculation["P"],
            "D_star": counterfactual_calculation["D_star"],
            "score": counterfactual_calculation["score"],
            "status": counterfactual_status,
        }

    states_read = []
    for event in default_order(list(facts.referenced_events.values())):
        states_read.append({
            "id": event.transaction_id,
            "state": event.state,
            "since": iso(event.state_since),
            "instant": iso(event.instant),
            "amount": event.amount,
        })

    reasoning = {
        "cut": {
            "received_at": iso(current.received_at),
            "instant": iso(current.instant),
            "policy_version": policy_version,
            "two_factor_route_enabled": two_factor_route_enabled,
        },
        "derived": facts.summary(),
        "states_read": states_read,
        "floors": floors,
        "rules": rules + [escalation],
        "attenuators": attenuators,
        "suppressions": suppressions,
        "score": calculation,
        "independent_rejection": {
            "counterfactual": counterfactual,
            "value": independent_rejection,
        },
        "decision": {
            "status": status,
            "decision_basis": decision_basis,
            "fired_floors": fired_floors,
        },
    }

    return {
        "status": status,
        "decision_basis": decision_basis,
        "score": calculation["score"],
        "score_band": calculation["score_band"],
        "independent_rejection": independent_rejection,
        "reasoning": reasoning,
    }
