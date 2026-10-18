"""A fórmula e a precedência (seção 6).

    pontos[g] = min(soma das regras do grupo g; teto[g])
    P         = Σ pontos[g] + min(S12; 15)
    D         = A1 + A2 + A3 + A4
    D*        = min(D; 30; ⌊P ÷ 2⌋)
    score     = min(100; max(0; P − D*))
"""
from typing import List

from controllers.policy import parameters

APPROVED = "approved"
TWO_FACTOR_REQUIRED = "two_factor_required"
REJECTED = "rejected"

BASIS_SCORE = "score"
BASIS_FLOOR = "floor_2fa"


def score_band(score: int) -> str:
    if score >= parameters.BAND_HIGH_FROM:
        return "high"

    if score >= parameters.BAND_MEDIUM_FROM:
        return "medium"

    return "low"


def calculate(rules: List[dict], s12_points: int, discounts: dict) -> dict:
    """Os pontos por grupo, P, D, D*, o score, a faixa e a diversidade.

    `discounts` traz o valor de cada atenuante já decidido — é por ele
    que o recálculo da rejeição independente troca o A4 reduzido pelo
    A4 cheio sem recalcular regra nenhuma.
    """
    raw_points = {}
    for group in parameters.GROUP_CAPS:
        raw_points[group] = 0

    for rule in rules:
        raw_points[rule["group"]] = raw_points[rule["group"]] + rule["points"]

    capped_points = {}
    for group, cap in parameters.GROUP_CAPS.items():
        capped_points[group] = min(raw_points[group], cap)

    escalation = min(s12_points, parameters.S12_CAP)
    total_points = sum(capped_points.values()) + escalation

    total_discount = sum(discounts.values())
    effective_discount = min(total_discount, parameters.MAX_DISCOUNT, total_points // 2)

    score = min(100, max(0, total_points - effective_discount))

    diverse_groups = []
    for group in parameters.DIVERSITY_GROUPS:
        if capped_points[group] >= parameters.DIVERSITY_MIN_POINTS:
            diverse_groups.append(group)

    return {
        "points_before_cap": raw_points,
        "points_after_cap": capped_points,
        "S12": escalation,
        "P": total_points,
        "attenuators": discounts,
        "D": total_discount,
        "D_star": effective_discount,
        "score": score,
        "score_band": score_band(score),
        "diverse_groups": diverse_groups,
        "diverse": len(diverse_groups) >= parameters.DIVERSITY_MIN_GROUPS,
    }


def precedence(calculation: dict, fired_floors: List[str]) -> tuple:
    """(status, decision_basis). A faixa não é o status: a diversidade decide se high vira rejected."""
    band = calculation["score_band"]

    if band == "high" and calculation["diverse"]:
        return REJECTED, BASIS_SCORE

    if band in ("high", "medium"):
        return TWO_FACTOR_REQUIRED, BASIS_SCORE

    if fired_floors:
        return TWO_FACTOR_REQUIRED, BASIS_FLOOR

    return APPROVED, BASIS_SCORE
