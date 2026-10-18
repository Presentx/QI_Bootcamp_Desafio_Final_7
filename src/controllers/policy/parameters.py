"""Os parâmetros [POL] da política 6.0.0, num lugar só.

Todo número que decide alguma coisa mora aqui, com o nome da regra que
o usa. São valores iniciais, sem calibração com dados reais: quando a
calibração chegar, ela muda este arquivo — e a versão da política em
src/constants.py — sem tocar na lógica das regras.

Dinheiro sempre em centavos, inteiro. "Acima de" é sempre estrito: o
valor igual ao limite não dispara, um centavo acima dispara.
"""
from datetime import timedelta

# ── Teto da faixa por idade da conta de origem (seção 4) ─────────────
# (idade mínima em dias, teto PF, teto PJ), do mais novo ao mais velho.
CEILING_BY_ACCOUNT_AGE = [
    (0, 100_000, 500_000),
    (7, 300_000, 1_500_000),
    (30, 1_000_000, 5_000_000),
    (90, 2_000_000, 10_000_000),
]

NEW_ACCOUNT_DAYS = 30

# ── Definições derivadas (seção 3.4) ─────────────────────────────────
ACTIVE_DAY_MIN_AMOUNT = 2_000          # dia ativo / dia bom / maturidade
RELATION_EVIDENCE_MIN_AMOUNT = 1_000   # evidência de relação: ACIMA de 1.000
DEVICE_TRUSTED_MIN_EVIDENCES = 3
DEVICE_TRUSTED_MIN_DAYS = 2
DEVICE_TRUSTED_MIN_AGE = timedelta(days=7)
HABITUAL_LOCATION_WINDOW = timedelta(days=90)
HABITUAL_LOCATION_RADIUS_KM = 30
HABITUAL_LOCATION_MIN_DAYS = 3
NEW_PERSON_WINDOW = timedelta(days=30)

# ── Janelas de histórico ─────────────────────────────────────────────
# A consulta de janela do pagador cobre a maior janela retrospectiva
# (A4: 180 dias) e a maior prospectiva (S2: 24h depois de t).
PAYER_WINDOW_BACK = timedelta(days=181)
PAYER_WINDOW_FORWARD = timedelta(days=1)
FULL_HISTORY_LIMIT = 1000

# ── Pisos (seção 5.1) ────────────────────────────────────────────────
F1_WINDOW = timedelta(days=30)
F1_MIN_EVENTS = 5
F1_MIN_AMOUNT = 20_000

F2_MIN_AMOUNT = 50_000
F2_MATURITY_MIN_EVIDENCES = 5
F2_MATURITY_MIN_DAYS = 3

F3_MIN_AMOUNT = 20_000

F4_MIN_AMOUNT = 50_000

F5_MIN_AMOUNT = 500_000

F6_NIGHT_LIMIT = 100_000

F7_BASE_LIMIT = 1_000_000
F7_MAX_LIMIT = 5_000_000
F7_REFERENCE_WINDOW = timedelta(days=90)
F7_MATURITY_MIN_EVENTS = 10
F7_MATURITY_MIN_NIGHTS = 5
F7_MATURITY_MIN_AGE = timedelta(days=30)

F8_DAY_LIMIT = 100_000

F9_WINDOW = timedelta(minutes=60)

F10_TEST_WINDOW = timedelta(minutes=30)
F10_MAX_TEST_AMOUNT = 1_000
F10_MIN_AMOUNT = 100_000
F10_TEST_MULTIPLIER = 20

# F11 — rampa de entrada (seção 5.1.1): (dias bons mínimos, limite PF, limite PJ)
F11_LEVELS = [
    (0, 100_000, 500_000),
    (1, 200_000, 1_000_000),
    (3, 500_000, 2_500_000),
    (5, 1_000_000, 5_000_000),
]
F11_MAX_DAILY_REQUESTS_PF = 10
F11_MAX_DAILY_REQUESTS_PJ = 50

# ── Score (seção 5.2) ────────────────────────────────────────────────
GROUP_VALUE = "valor"
GROUP_DEVICE = "dispositivo"
GROUP_VELOCITY = "velocidade"
GROUP_COUNTERPARTY = "contraparte"
GROUP_LOCATION = "local"
GROUP_TIME = "horario"

GROUP_CAPS = {
    GROUP_VALUE: 40,
    GROUP_DEVICE: 25,
    GROUP_VELOCITY: 20,
    GROUP_COUNTERPARTY: 35,
    GROUP_LOCATION: 25,
    GROUP_TIME: 10,
}

# Horário e Escalada nunca contam para a diversidade.
DIVERSITY_GROUPS = [GROUP_VALUE, GROUP_DEVICE, GROUP_VELOCITY, GROUP_COUNTERPARTY, GROUP_LOCATION]
DIVERSITY_MIN_POINTS = 15
DIVERSITY_MIN_GROUPS = 2

S1_POINTS = 25

S2_WINDOW = timedelta(hours=24)
S2_CEILING_MULTIPLIER = 2
S2_POINTS = 20

S3_WINDOW = timedelta(days=90)
S3_MAX_REFERENCES = 30
S3_MIN_REFERENCES = 5
S3_MIN_AMOUNT = 50_000
S3_LOW_MULTIPLIER = 5
S3_LOW_POINTS = 25
S3_HIGH_MULTIPLIER = 10
S3_HIGH_POINTS = 35

S4_MULTIPLIER = 3
S4_FALLBACK_AMOUNT = 500_000
S4_POINTS = 25

S5_MIN_AMOUNT = 20_000
S5_NEW_DEVICE_POINTS = 25
S5_OBSERVED_DEVICE_POINTS = 15

S6_SHORT_WINDOW = timedelta(minutes=10)
S6_LONG_WINDOW = timedelta(minutes=60)
S6_THRESHOLDS = {"PF": (5, 10), "PJ": (30, 100)}
S6_POINTS = 20

S7_VERY_NEW_DAYS = 7
S7_VERY_NEW_POINTS = 25
S7_VERY_NEW_PF_TO_PF_POINTS = 30
S7_NEW_POINTS = 10
S7_NEW_PF_TO_PF_POINTS = 15

S8_WINDOW = timedelta(hours=24)
S8_MIN_PAYERS = 5
S8_POINTS = 20

S9_DISTANCE_KM = 300
S9_POINTS = 15

S10_MIN_DISTANCE_KM = 300
S10_MAX_SPEED_KMH = 900
S10_POINTS = 25

S11_WINDOW = timedelta(days=90)
S11_MIN_REFERENCES = 10
S11_MAX_SHARE = 0.10
S11_POINTS = 10

S12_WINDOW = timedelta(minutes=10)
S12_POINTS = 15
S12_CAP = 15

# ── Atenuantes (seção 5.3) ───────────────────────────────────────────
A1_MIN_EVIDENCES = 3
A1_MIN_DAYS = 3
A1_MIN_AGE = timedelta(days=14)
A1_LAST_EVIDENCES = 12
A1_DISCOUNT = 10

A2_MIN_DESTINATION_AGE_DAYS = 90
A2_DISCOUNT = 10

A3_NO_GEO_WINDOW = timedelta(days=30)
A3_DISCOUNT = 10

A4_WINDOW_START = timedelta(days=180)
A4_WINDOW_END = timedelta(hours=24)
A4_MAX_DISCOUNT = 15
A4_REJECTION_WINDOW = timedelta(days=7)

# ── Fórmula (seção 6) ────────────────────────────────────────────────
MAX_DISCOUNT = 30
BAND_MEDIUM_FROM = 40
BAND_HIGH_FROM = 70

# ── 2FA (seção 8) ────────────────────────────────────────────────────
TWO_FACTOR_DEADLINE = timedelta(minutes=10)
