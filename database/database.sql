-- Motor Antifraude Pix — política 6.0.0
--
-- Três tabelas de negócio e uma de enumerador:
--
--   analysis_status   a lista fechada das decisões (approved, two_factor_required, rejected)
--   pix_transaction   o Pix como chegou, já normalizado: o histórico de tudo que entrou
--   analysis          a decisão sobre ele, com a resposta congelada e a fundamentação
--   two_factor_event  o resultado do 2FA (rota E1), uma linha no máximo por análise
--
-- Instantes são TIMESTAMPTZ: o offset do payload serve só para achar o
-- instante, e todas as janelas da política comparam instantes absolutos.

CREATE TABLE analysis_status(
    id                              SERIAL PRIMARY KEY,
    enumerator                      VARCHAR(50) NOT NULL,
    created_at                      TIMESTAMP NOT NULL DEFAULT(NOW()),
    UNIQUE(enumerator)
);

INSERT INTO analysis_status (enumerator) VALUES
('approved'),
('two_factor_required'),
('rejected');

CREATE TABLE pix_transaction(
    id                              SERIAL PRIMARY KEY,
    transaction_id                  VARCHAR(64) NOT NULL,
    amount                          BIGINT NOT NULL CHECK (amount BETWEEN 1 AND 1000000000),
    event_instant                   TIMESTAMPTZ NOT NULL,
    received_at                     TIMESTAMPTZ NOT NULL,
    payer_document                  VARCHAR(14) NOT NULL,
    payer_type                      CHAR(2) NOT NULL CHECK (payer_type IN ('PF', 'PJ')),
    source_branch                   VARCHAR(20) NOT NULL,
    source_account                  VARCHAR(20) NOT NULL,
    source_opening_date             TIMESTAMPTZ NOT NULL,
    destination_participant         CHAR(8) NOT NULL,
    destination_branch              VARCHAR(20) NOT NULL,
    destination_account             VARCHAR(20) NOT NULL,
    destination_opening_date        TIMESTAMPTZ NOT NULL,
    destination_document            VARCHAR(14) NOT NULL,
    destination_type                CHAR(2) NOT NULL CHECK (destination_type IN ('PF', 'PJ')),
    device_id                       VARCHAR(128) NOT NULL,
    channel                         VARCHAR(20) NOT NULL,
    ip                              VARCHAR(45) NOT NULL,
    latitude                        NUMERIC(9, 6),
    longitude                       NUMERIC(9, 6),
    payload                         JSONB NOT NULL,
    created_at                      TIMESTAMP NOT NULL DEFAULT(NOW()),
    UNIQUE(transaction_id),
    CHECK ((latitude IS NULL) = (longitude IS NULL))
);

CREATE INDEX pix_transaction_payer_instant_idx
    ON pix_transaction (payer_document, event_instant);
CREATE INDEX pix_transaction_payer_received_idx
    ON pix_transaction (payer_document, received_at);
CREATE INDEX pix_transaction_payer_device_idx
    ON pix_transaction (payer_document, device_id, event_instant);
CREATE INDEX pix_transaction_destination_idx
    ON pix_transaction (destination_participant, destination_branch, destination_account, event_instant);
CREATE INDEX pix_transaction_relationship_idx
    ON pix_transaction (payer_document, destination_participant, destination_branch, destination_account, event_instant);
CREATE INDEX pix_transaction_payer_recipient_idx
    ON pix_transaction (payer_document, destination_document);

CREATE TABLE analysis(
    id                              SERIAL PRIMARY KEY,
    analysis_key                    CHAR(36) NOT NULL,
    pix_transaction_id              INTEGER NOT NULL REFERENCES pix_transaction(id),
    status_id                       INTEGER NOT NULL REFERENCES analysis_status(id),
    score                           SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
    score_band                      VARCHAR(10) NOT NULL,
    decision_basis                  VARCHAR(10) NOT NULL,
    independent_rejection           BOOLEAN NOT NULL,
    policy_version                  VARCHAR(10) NOT NULL,
    two_factor_deadline             TIMESTAMPTZ,
    response                        JSONB NOT NULL,
    reasoning                       JSONB NOT NULL,
    request_id                      VARCHAR(64),
    created_at                      TIMESTAMP NOT NULL DEFAULT(NOW()),
    UNIQUE(analysis_key),
    UNIQUE(pix_transaction_id)
);

CREATE TABLE two_factor_event(
    id                              SERIAL PRIMARY KEY,
    analysis_id                     INTEGER NOT NULL REFERENCES analysis(id),
    result                          VARCHAR(10) NOT NULL CHECK (result IN ('confirmed', 'declined', 'cancelled')),
    recorded_at                     TIMESTAMPTZ NOT NULL,
    request_id                      VARCHAR(64),
    created_at                      TIMESTAMP NOT NULL DEFAULT(NOW()),
    UNIQUE(analysis_id)
);
