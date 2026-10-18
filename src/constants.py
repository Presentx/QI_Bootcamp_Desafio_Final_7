import os


SERVICE_ROOT = os.path.abspath(os.path.dirname(__file__))

# Onde moram os arquivos de schema — os .json que descrevem o formato
# que cada requisicao precisa ter. Quem le essa pasta e o
# src/utils/schema_handler.py.
SCHEMA_PATH = os.path.join(SERVICE_ROOT, "schemas")

APP_ENV = os.environ.get("APP_ENV", "local")
SERVICE_NAME = os.environ.get("SERVICE_NAME", "antifraude-pix-api")

DATABASE_URL = os.environ.get("DATABASE_URL")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN")

# A versão da política que este motor aplica. Vai em toda resposta e em
# toda fundamentação: uma decisão gravada sempre diz com qual régua foi
# tomada, mesmo depois de a régua mudar.
POLICY_VERSION = "6.0.0"

# A rota de confirmação de 2FA (E1). Com ela ligada, um 2FA sem resposta
# expira em 10 minutos e vira NC; desligada, todo 2FA fica PE para sempre
# (seção 8 da política). A flag vai gravada em cada fundamentação.
TWO_FACTOR_ROUTE_ENABLED = os.environ.get("TWO_FACTOR_ROUTE_ENABLED", "true").lower() == "true"

# Quanto tempo uma análise espera pela trava do id ou do pagador antes de
# desistir com 503. Sem prazo, uma transação presa seguraria todas as
# outras do mesmo pagador.
LOCK_TIMEOUT = os.environ.get("LOCK_TIMEOUT", "2s")

# O relógio de teste. Os cenários da política têm histórico de meses, e a
# admissibilidade só aceita transaction_date perto do relógio do servidor:
# sem poder fixar o "agora", nenhum teste de caixa-preta montaria o
# histórico pela API. Com APP_ENV local ou test, o cabeçalho abaixo
# substitui o relógio do servidor naquela requisição. Em production ele é
# ignorado — veja src/middlewares/test_clock.py.
TEST_CLOCK_HEADER = "X-Test-Clock"
TEST_CLOCK_ENABLED = APP_ENV.lower() in ("local", "test")

# Rotas públicas: não exigem o header INTERNAL-TOKEN. São as duas que
# precisam responder pra quem ainda não tem token nenhum: a raiz, que
# diz quem é este serviço, e o health check, que o Docker consulta pra
# saber se a API já está de pé.
BYPASS_ENDPOINTS = [
    "/",
    "/health_check",
]

REQUIRED_VARIABLES = ["DATABASE_URL", "INTERNAL_TOKEN"]


def check_variables():
    missing = []
    for name in REQUIRED_VARIABLES:
        if not globals().get(name):
            missing.append(name)

    if missing:
        raise EnvironmentError(
            f"Faltam variáveis de ambiente: {', '.join(missing)}. "
            "Rodando com 'docker compose up' elas já vêm preenchidas. "
            "Fora do Docker, copie o .env.example para .env."
        )
