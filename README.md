# Grupo 7

**Integrantes:** Gabriel de Castilho Valdo e Rodrigo Rampani Costa

Motor antifraude Pix — **política 6.0.0**. Uma API que recebe um Pix, aplica
as regras da política e devolve `approved`, `two_factor_required` ou
`rejected`, gravando cada análise com a fundamentação completa. Construída
a partir do Base Service (Python 3.11, FastAPI, JSON Schema, SQLAlchemy 2.0,
PostgreSQL 16, Docker), com as mesmas camadas e testes de caixa-preta por HTTP.

## Como o motor de decisão funciona

```
POST /pix/transaction
  middlewares        request id → log → INTERNAL-TOKEN (403) → relógio de teste → sessão
  schema             estrutura (400 QIT000001)
  controller         normaliza; CPF/CNPJ (422 QIT001003); abertura das contas (422 QIT001005)
    BEGIN
    trava "id:"      pg_advisory_xact_lock, timeout 2 s (503 QIT001006)
    trava "pagador:" sempre nesta ordem
    id já existe?    → 200 com a resposta congelada (sem nova análise)
    received_at      relógio do servidor, lido depois das travas
    admissível?      received_at − 24h ≤ t ≤ received_at + 5 min (422 QIT001004)
    histórico        uma consulta por necessidade, todas com received_at < received_at atual
    motor            11 pisos → 12 regras de score → 4 atenuantes → fórmula → precedência
    grava            pix_transaction + analysis (resposta congelada + fundamentação)
    COMMIT           só então responde 201
```

Três ideias sustentam o desenho:

1. **Dois relógios.** O instante do `transaction_date` (relógio de evento)
   define todas as janelas; o `received_at` do servidor (relógio de
   recebimento) define a admissibilidade e o **corte do histórico**: cada
   análise só enxerga o que foi recebido antes dela, no estado vigente
   naquele instante. Isso torna a decisão reprodutível — a fundamentação
   gravada lista exatamente os eventos e estados que foram lidos.
2. **Estados do histórico.** Cada evento é lido como AP (aprovado), RJ
   (rejeitado), PE (2FA pendente), AU (2FA confirmado) ou NC (2FA recusado,
   cancelado ou expirado). *Exposição* (o que conta nos limites) = AP, PE,
   AU. *Evidência boa* (o que gera confiança) = AP ou AU com instante
   anterior a t. Um 2FA pendente pesa nos limites como se tivesse sido pago,
   mas não constrói confiança.
3. **Travas por pagador.** Limites acumulados (noite, dia, 60 min) só são
   confiáveis se duas análises do mesmo pagador não rodarem ao mesmo tempo.
   A trava `pagador:` serializa as análises e confirmações do mesmo CPF/CNPJ;
   a trava `id:` serializa repetições do mesmo Pix.

O motor (`src/controllers/policy/`) não fala com o banco nem com HTTP:
recebe a transação normalizada e o histórico já no estado do corte, e
devolve a decisão com a fundamentação. Todos os parâmetros estão em
[`src/controllers/policy/parameters.py`](src/controllers/policy/parameters.py).

Valores sempre em **centavos**. Toda condição "acima de" é estrita: o valor
igual ao limite não dispara, um centavo acima dispara.

### Definições usadas pelas regras

| Termo | Definição |
|---|---|
| Pagador | documento (só dígitos) do titular da conta de origem — agrega **todas** as contas de origem do mesmo CPF/CNPJ |
| Teto da faixa | pela idade da conta de origem: PF 100.000 (0–6 d), 300.000 (7–29 d), 1.000.000 (30–89 d), 2.000.000 (90+ d); PJ 5× esses valores |
| Dispositivo novo / observado / confiável | 0 evidências boas com o (pagador, device_id) / ≥ 1 / ≥ 3 evidências em ≥ 2 dias civis, a primeira há ≥ 7 dias |
| Local habitual | ponto de evidência boa nos últimos 90 dias com evidências num raio de 30 km em ≥ 3 dias civis |
| Destino conhecido | existe evidência boa > 1.000 do pagador para a conta de destino |
| Pessoa nova | pagador sem eventos, ou com o primeiro evento há menos de 30 dias |
| Horário civil | America/Sao_Paulo: noite = [20:00, 06:00); dia civil; fim de semana |
| Janela máxima | para F9, S2 e S6, avalia (X − W, X] para X = t e para cada evento em (t, t + W) e fica com a maior — assim um Pix datado no passado e recebido atrasado enxerga o que veio depois dele |

## Regras

### Pisos de 2FA — cada um, sozinho, garante no mínimo `two_factor_required`

| Regra | Lógica | Parâmetros | Por quê |
|---|---|---|---|
| **F1** Geolocalização ausente atípica (PF) | ≥ 5 eventos nos últimos 30 dias, todos com geolocalização, e a atual sem | valor > 20.000 | Sumir com a localização de repente pode ser ocultação; quem nunca mandou ou começou a mandar não é punido |
| **F2** Aquecimento (PF) | pagador não maduro e destino não conhecido | valor > 50.000; maduro = ≥ 5 evidências boas ≥ 2.000 em ≥ 3 dias | Golpe típico em conta recém-tomada: valor alto para alguém novo, sem histórico que o sustente |
| **F3** PF→PF com as duas contas novas | titulares PF diferentes, origem e destino com < 30 dias | valor > 20.000 | Padrão de rede de contas-laranja; abaixo disso resta o S7 |
| **F4** PJ pagando CPF novo | nenhuma evidência boa da PJ para aquele CPF em todo o histórico | valor > 50.000 | Desvio de folha/fornecedor para pessoa física desconhecida; o primeiro pagamento confirmado libera os próximos |
| **F5** PJ com conta nova e valor alto | conta de origem PJ com < 30 dias | valor > 500.000 | Mede a idade da conta, não da empresa |
| **F6** Noturno PF→PF | exposição do pagador (todas as contas) na noite que contém t + atual | > 100.000 | Inspirada no limite noturno da Res. BCB 142/2021; somar por CPF impede dividir o limite entre contas |
| **F7** Noturno PJ | exposição da noite + atual > L | L = 1.000.000; com maturidade noturna, L = max(1M; min(5M; 2 × mediana das noites dos últimos 90 d)) | O limite acompanha o padrão noturno real da empresa, com piso e teto |
| **F8** Dispositivo não confiável (PF) | exposição do dispositivo no dia civil + atual | > 100.000 | Aproxima a regra do Regulamento Pix para dispositivos não cadastrados |
| **F9** Teto da faixa em 60 min | exposição do pagador na janela máxima de 60 min + atual | > teto da faixa | Cobre valor único acima do teto, fracionamento e eventos atrasados |
| **F10** Teste seguido de valor alto | conta de destino < 30 d; Pix de até 1.000 para ela em (t − 30 min, t], sem relação anterior | atual ≥ 100.000 e ≥ 20× o teste | "Pix de teste" para validar a conta antes do golpe; testes ≤ 1.000 nunca viram relação, então vários testes não se escondem uns atrás dos outros |
| **F11** Rampa de entrada | pessoa nova; exposição do dia civil + atual > limite do nível | nível 0/1/2/3 por dias bons (0, 1–2, 3–4, 5+): PF 100k/200k/500k/1M, PJ 500k/1M/2,5M/5M; −1 nível por rejeição independente ou 2FA NC; > 10 pedidos no dia (PF) ou > 50 (PJ) → nível 0; nunca acima do teto | Limita o quanto alguém recém-chegado movimenta por dia enquanto ainda não tem histórico, e reage a tentativas malsucedidas |

### Score — pontos por grupo, cada grupo com teto

| Regra | Grupo (teto) | Lógica | Pontos | Por quê |
|---|---|---|---|---|
| **S1** Valor acima do teto | Valor (40) | valor > teto da faixa | +25 | Limite simples pela maturidade da conta |
| **S2** Exposição de 24h | Valor | janela máxima de 24h + atual > 2 × teto | +20 | Montante acumulado no dia |
| **S3** Desvio da mediana | Valor | até 30 evidências boas dos últimos 90 d (≥ 5; PJ separado por tipo de recebedor); valor ≥ 50.000 e > 5× M (+25) ou > 10× M (+35); suprimida por A1 | +25/+35 | Fora do padrão do próprio cliente; a mediana resiste a um pagamento isolado |
| **S4** PJ→PF fora do expediente | Valor | [20:00, 07:00) ou fim de semana; > 3× mediana PJ→PF (≥ 5 refs) ou > 500.000 | +25 | Folha e reembolso acontecem em horário comercial |
| **S5** Dispositivo | Dispositivo (25) | valor > 20.000 e dispositivo novo (+25) ou observado não confiável (+15) | +25/+15 | Aparelho desconhecido é o principal sinal de conta tomada |
| **S6** Tentativas | Velocidade (20) | eventos em qualquer estado + atual: PF ≥ 5 em 10 min ou ≥ 10 em 60 min (PJ 30/100) | +20 | Robô ou golpista testando limites |
| **S7** Conta de destino nova | Contraparte (35) | 0–6 dias: +25 (PF→PF +30); 7–29 dias: +10 (PF→PF +15) | até +30 | Contas-laranja são abertas pouco antes do golpe |
| **S8** Conta PF nova com pagadores novos | Contraparte | titular PF, conta < 30 d, ≥ 5 pagadores distintos cujo primeiro Pix para ela caiu nas últimas 24h (o atual entra provisoriamente) | +20 | Conta-mula recebendo de muitas vítimas |
| **S9** Fora dos locais habituais | Local (25) | há local habitual e a atual está a > 300 km de todos | +15 | Mudança brusca de região |
| **S10** Deslocamento improvável (PF) | Local | evento anterior com geolocalização a ≥ 300 km e velocidade > 900 km/h | +25 | Ninguém está em SP e no Rio no mesmo minuto |
| **S11** Horário atípico | Horário (10) | PF à noite (PJ: noite ou fim de semana) com < 10% das ≥ 10 evidências dos últimos 90 d nesse horário; suprimida por S4 | +10 | Fora do hábito do cliente, mas fraco sozinho |
| **S12** Insistência após rejeição | Escalada (15) | rejeição **independente** do pagador nos últimos 10 min | +15 | Quem é barrado e insiste logo em seguida |

### Atenuantes — descontos

| Regra | Lógica | Desconto | Por quê |
|---|---|---|---|
| **A1** Destino recorrente | ≥ 3 evidências de relação em ≥ 3 dias, a primeira há ≥ 14 dias; aplica-se se 2 × valor ≤ 3 × mediana das últimas 12; suprime S3 | −10 | O aluguel de todo mês não deve parecer anomalia |
| **A2** Mesma titularidade | mesmo documento na origem e no destino, destino com ≥ 90 dias | −10 | Transferência para conta própria antiga |
| **A3** Contexto confiável | dispositivo confiável e (a até 30 km de um local habitual, ou sem geolocalização num pagador que não manda geolocalização há 30 dias) | −10 | Aparelho e lugar de sempre |
| **A4** Confiança contínua | dias ativos (evidência boa ≥ 2.000) em (t − 180 d, t − 24h]; ⌊dias ÷ 2⌋ até 15; metade se houve rejeição independente nos últimos 7 d | até −15 | Histórico longo e bom pesa a favor, mas perde força depois de uma rejeição |

Todos os parâmetros são valores iniciais da política, **sem calibração com
dados reais**; ficam centralizados em `parameters.py` para que a calibração
mude só um arquivo (e a versão da política).

## Como as regras viram decisão

```
pontos[g] = min(soma das regras do grupo; teto do grupo)
P         = Σ pontos[g] + min(S12; 15)
D         = A1 + A2 + A3 + A4
D*        = min(D; 30; ⌊P ÷ 2⌋)          ← atenuante nunca apaga mais da metade dos pontos
score     = min(100; max(0; P − D*))
faixa     = low (0–39) | medium (40–69) | high (70–100)
diverso   = ≥ 2 grupos entre Valor, Dispositivo, Velocidade, Contraparte e Local com ≥ 15 pontos
```

Ordem de precedência:

1. **high e diverso → `rejected`** (`decision_basis = score`). Rejeitar exige
   sinais fortes de pelo menos duas famílias diferentes: dispositivo e local
   vêm do aparelho do cliente e podem ser falsificados, então nunca rejeitam
   sozinhos.
2. **high sem diversidade, ou medium → `two_factor_required`** (`score`).
3. **low com algum piso → `two_factor_required`** (`floor_2fa`).
4. **low sem piso → `approved`** (`score`).

**Rejeição independente:** uma rejeição só alimenta S12, a redução de A4 e o
rebaixamento da rampa F11 se continuar `rejected` no recálculo sem S12 e com
A4 cheio. Isso quebra o ciclo "uma rejeição causa a próxima".

**2FA (rota E1, ligada por padrão):** a instituição informa o resultado em
`PUT /pix/transaction/{id}/two_factor` até 10 minutos depois do recebimento.
`confirmed` vira AU (evidência boa para análises recebidas depois);
`declined`/`cancelled` ou sem resposta no prazo viram NC (sai dos limites,
rebaixa a rampa, mas não é tratado como fraude). A resposta do POST continua
congelada; o resultado aparece no GET.

### Exemplo de resposta

```json
{
  "key": "b3f1c2e0-7d4a-4c1e-9a52-1f0e6c9d2a77",
  "status": "two_factor_required",
  "metadata": {"score": 13, "score_band": "low", "decision_basis": "floor_2fa", "policy_version": "6.0.0"}
}
```

`GET /pix/transaction/{id}` devolve a mesma resposta e, à parte, o payload
normalizado gravado, o estado do 2FA e a **fundamentação**: corte
(`received_at`, instante, versão), definições derivadas (teto, grau do
dispositivo, locais habituais, maturidade…), estados lidos de cada evento
referenciado, cada piso e regra (disparou ou não, valor observado, limiar,
janela usada), supressões, pontos por grupo antes e depois do teto, P, D,
D*, diversidade e o recálculo da rejeição independente.

## Como rodar e testar

Pré-requisitos: Docker (com `docker compose`) e Python 3.11+ para os testes.

```bash
docker compose up
```

A API sobe em http://localhost:3000 (o banco nasce com `database/database.sql`).
Se você já tinha subido o projeto base antes, recrie o banco: `docker compose down -v`.

Um Pix (Git Bash no Windows):

```bash
curl -X POST http://localhost:3000/pix/transaction \
  -H "INTERNAL-TOKEN: default_token" -H "Content-Type: application/json" \
  -d '{
    "id": "pix-000123",
    "amount": 150000,
    "transaction_date": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
    "source_account": {"branch": "0001", "account_number": "88213",
      "opening_date": "2025-03-02T10:00:00-03:00", "owner": {"document_number": "123.456.789-09"}},
    "destination_account": {"participant": "60701190", "branch": "3675", "account_number": "10442",
      "opening_date": "2026-10-10T09:00:00-03:00", "owner": {"document_number": "987.654.321-00"}},
    "source": {"device_id": "dvc-9f2c71a8", "channel": "app", "ip": "198.51.100.23",
      "geolocation": {"latitude": -23.56, "longitude": -46.65}}
  }'

curl http://localhost:3000/pix/transaction/pix-000123 -H "INTERNAL-TOKEN: default_token"
```

(O `transaction_date` precisa estar entre 24h antes e 5 min depois do relógio
do servidor; por isso o exemplo usa o horário atual.)

Testes (com a API de pé, em outro terminal):


```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv/Scripts/activate
pip install -r requirements-dev.txt
pytest
python3 -m flake8 src tests
```

> No Windows, se os testes não conectarem em `0.0.0.0`, rode com
> `SERVER_LOCALHOST=127.0.0.1`.
> python3 = python

São 80 testes de caixa-preta por HTTP em `tests/integration/`, cobrindo os
cenários da matriz da política (casos 1–30, 16c, 18a–i, 19, 20', 20'', 21,
22-A/B, 23a–c', 24a–c, 26a–c, 27a/c, 28a–h, 29a–d, 30a–c) mais validação,
idempotência e concorrência. O `test_edge_cases_and_breaches.py` cobre 10
casos de borda: contrato do GET, id com espaços, datas inexistentes, S10 com
Δt = 0, evasão do F10 com centavos, F6 com duas contas do mesmo CPF, 2FA
pendente na janela do F9, mediana do S3 com micropagamentos, idempotência com
payload adulterado e 16 requisições simultâneas.

**Como os testes montam meses de histórico pela API.** Os cenários têm
históricos desde agosto, mas a admissibilidade só aceita datas perto do
relógio do servidor. Com `APP_ENV` `local` ou `test` (o padrão do compose),
o cabeçalho `X-Test-Clock: <ISO 8601 com offset>` fixa o "agora" do servidor
naquela requisição — cada Pix do histórico entra por `POST`, como em
produção, só que "recebido" na data do cenário. Com `APP_ENV=production` o
cabeçalho é ignorado. O teste de concorrência usa o relógio real.


### Rotas

| Método e rota | Faz | Responde |
|---|---|---|
| `GET /` | quem é este serviço | `200` |
| `GET /health_check` | está de pé? | `204` |
| `POST /pix/transaction` | analisa um Pix (idempotente pelo `id`) | `201` análise nova; `200` id repetido (resposta congelada); `400` `QIT000001`; `403` `QIT000002`; `422` `QIT001003`/`QIT001004`/`QIT001005`; `503` `QIT001006`/`QIT001009` |
| `GET /pix/transaction/{id}` | a análise gravada + fundamentação | `200`; `404` `QIT001001` |
| `PUT /pix/transaction/{id}/two_factor` | resultado do 2FA: `{"result": "confirmed" \| "declined" \| "cancelled"}` | `200`; `400`; `404` `QIT001001`; `409` `QIT001007` (resultado diferente de um estado final) / `QIT001008` (prazo expirado) |

Uma rejeição antifraude é **`201` com `status: rejected`**: a análise foi
feita e gravada; o resultado é que foi "não".

### Códigos de erro

| Código | HTTP | Quando |
|---|---|---|
| `QIT000001` | 400 | estrutura inválida (schema, data sem offset ou impossível, IP inválido) |
| `QIT000002` | 403 | `INTERNAL-TOKEN` ausente ou errado |
| `QIT001001` | 404 | análise não encontrada |
| `QIT001003` | 422 | dígito verificador de CPF/CNPJ inválido (pagador ou recebedor) |
| `QIT001004` | 422 | `transaction_date` fora da admissibilidade (só id novo) |
| `QIT001005` | 422 | abertura de conta posterior à transação |
| `QIT001006` | 503 | banco ou trava indisponível; nada gravado — repita o mesmo id |
| `QIT001007` | 409 | E1: resultado diferente de um estado final |
| `QIT001008` | 409 | E1: prazo do 2FA expirado |
| `QIT001009` | 503 | resultado do COMMIT indeterminado — repita o mesmo id |
| `QIT000500` | 500 | erro inesperado |

### Modelagem de dados

| Tabela | Guarda |
|---|---|
| `analysis_status` | enumerador: `approved`, `two_factor_required`, `rejected` |
| `pix_transaction` | o Pix normalizado (documentos só com dígitos, instantes em UTC, coordenadas com 6 casas), `event_instant`, `received_at`, `UNIQUE(transaction_id)` e os índices de histórico (pagador × instante, pagador × recebimento, pagador × dispositivo, conta de destino, relacionamento, pagador × documento do recebedor) |
| `analysis` | 1:1 com o Pix: `key` (UUID público), status, score, faixa, base da decisão, rejeição independente, versão da política, prazo do 2FA, **resposta congelada** e **fundamentação** (JSONB), `request_id` |
| `two_factor_event` | no máximo um por análise (`UNIQUE`): resultado e instante da transição PE → AU/NC |

O `id` numérico de cada tabela nunca sai da API: o mundo de fora conhece o
`id` do Pix (dele) e a `key` da análise (nossa).

### Configuração

| Variável | Padrão | Para quê |
|---|---|---|
| `APP_ENV` | `local` | `local`/`test` ligam log DEBUG e o relógio de teste; `production` desliga |
| `INTERNAL_TOKEN` | `default_token` | token exigido em todas as rotas exceto `/` e `/health_check` |
| `TWO_FACTOR_ROUTE_ENABLED` | `true` | liga a rota E1 e a expiração do 2FA em 10 min |
| `LOCK_TIMEOUT` | `2s` | espera máxima pelas travas |

### Limitações aceitas

- Parâmetros, pesos e faixas são valores iniciais, sem calibração.
- Horário civil fixo em America/Sao_Paulo; clientes em outros fusos têm a noite deslocada.
- Decisões gravadas não são revistas quando chega um evento atrasado: o piso cai no evento atrasado.
- S8 não é travado por conta de destino: durante uma corrida N pode ficar subestimado (nunca contado em dobro).
- S3 mantém Pix pequenos na mediana: pega o aquecimento com centavos, ao custo de mais pontos para quem faz muitos Pix pequenos legítimos (caso 29d).
- Mesmo `id` com conteúdo diferente devolve a resposta original, sem nova análise.
- Com a E1 desligada, um 2FA nunca vira evidência boa e F4 pode se repetir para o mesmo CPF.
- F6 e F8 são inspirados em normas, sem verificação de conformidade (o contrato não traz limites configurados pelo cliente nem cadastro de dispositivos).
- Partida a frio: no início da operação todo pagador é "pessoa nova" e passa pela rampa F11.
