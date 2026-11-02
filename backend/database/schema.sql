-- GRCHub core schema (PostgreSQL). No seed data: all regulatory content comes from real documents.

-- ---------------------------------------------------------------- regulatory content (global)

CREATE TABLE frameworks (
    id    SERIAL PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,            -- e.g. NCA ECC, SAMA CSF
    kind  TEXT NOT NULL CHECK (kind IN ('controls_framework'))
);

CREATE TABLE framework_versions (
    id             SERIAL PRIMARY KEY,
    framework_id   INT NOT NULL REFERENCES frameworks(id),
    version_label  TEXT NOT NULL,
    effective_date DATE,
    UNIQUE (framework_id, version_label)
);

CREATE TABLE regulatory_documents (
    id                   SERIAL PRIMARY KEY,
    framework_version_id INT NOT NULL REFERENCES framework_versions(id),
    source_id            TEXT NOT NULL,       -- id in backend/acquisition/sources.yaml
    source_url           TEXT,                -- where it was retrieved from ("manual:<file>" if supplied by a user)
    sha256               CHAR(64) NOT NULL UNIQUE,
    storage_path         TEXT NOT NULL,
    language             TEXT,
    publication_date     TEXT,                -- as printed (may be only month/year); NULL if unknown
    retrieved_at         TIMESTAMPTZ NOT NULL
);

CREATE TABLE requirements (
    id                     SERIAL PRIMARY KEY,
    document_id            INT NOT NULL REFERENCES regulatory_documents(id),
    framework_version_id   INT NOT NULL REFERENCES framework_versions(id),
    control_id             TEXT NOT NULL,        -- normalized
    printed_id             TEXT NOT NULL,        -- as printed in the source
    id_is_derived          BOOLEAN NOT NULL DEFAULT FALSE,
    kind                   TEXT NOT NULL,        -- control | sub_control | control_consideration | control_reference
    level                  SMALLINT NOT NULL,
    parent_control_id      TEXT,
    domain                 TEXT,
    subdomain_id           TEXT,
    subdomain_title        TEXT,
    principle              TEXT,
    objective              TEXT,
    original_text          TEXT NOT NULL,        -- verbatim, never edited
    page_index             INT NOT NULL,         -- physical PDF page
    page_label             TEXT,                 -- page number printed on the page
    extraction_method      TEXT NOT NULL,
    extraction_confidence  REAL NOT NULL CHECK (extraction_confidence BETWEEN 0 AND 1),
    parse_notes            JSONB NOT NULL DEFAULT '[]',
    validation_status      TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (validation_status IN ('pending_review','approved','rejected')),
    reviewed_by            TEXT,
    reviewed_at            TIMESTAMPTZ,
    UNIQUE (framework_version_id, control_id)
);
CREATE INDEX requirements_status_idx ON requirements (validation_status);

-- Every review decision is kept. text_sha256 pins the decision to the exact text that was reviewed.
CREATE TABLE requirement_reviews (
    id              BIGSERIAL PRIMARY KEY,
    requirement_id  INT NOT NULL REFERENCES requirements(id),
    reviewer        TEXT NOT NULL,
    decision        TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
    note            TEXT,
    text_sha256     CHAR(64) NOT NULL,
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- SAMA-style maturity model (levels with criteria), extracted from the framework document.
CREATE TABLE maturity_levels (
    id                    SERIAL PRIMARY KEY,
    document_id           INT NOT NULL REFERENCES regulatory_documents(id),
    framework_version_id  INT NOT NULL REFERENCES framework_versions(id),
    level                 SMALLINT NOT NULL,
    name                  TEXT NOT NULL,
    criteria              JSONB NOT NULL,         -- "Definition and Criteria" bullets, verbatim
    explanation           JSONB NOT NULL,         -- "Explanation" bullets, verbatim
    page_index            INT NOT NULL,
    page_label            TEXT,
    extraction_confidence REAL NOT NULL CHECK (extraction_confidence BETWEEN 0 AND 1),
    parse_notes           JSONB NOT NULL DEFAULT '[]',
    validation_status     TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (validation_status IN ('pending_review','approved','rejected')),
    reviewed_by           TEXT,
    reviewed_at           TIMESTAMPTZ,
    UNIQUE (framework_version_id, level)
);

-- Narrative sections that are not controls (for example the maturity level 3/4/5 guidance).
CREATE TABLE framework_sections (
    id                    SERIAL PRIMARY KEY,
    document_id           INT NOT NULL REFERENCES regulatory_documents(id),
    framework_version_id  INT NOT NULL REFERENCES framework_versions(id),
    section_id            TEXT NOT NULL,
    title                 TEXT NOT NULL,
    original_text         TEXT NOT NULL,
    page_index            INT NOT NULL,
    page_label            TEXT,
    validation_status     TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (validation_status IN ('pending_review','approved','rejected')),
    reviewed_by           TEXT,
    reviewed_at           TIMESTAMPTZ,
    UNIQUE (framework_version_id, section_id)
);

-- ---------------------------------------------------------------- tenants and users

CREATE TABLE organizations (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Platform roles (org_id NULL) curate shared regulatory content; organization roles work on one org's data.
CREATE TABLE users (
    id             SERIAL PRIMARY KEY,
    org_id         INT REFERENCES organizations(id),
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('platform_admin','reviewer','org_admin','analyst','assessor','viewer')),
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((role IN ('platform_admin','reviewer') AND org_id IS NULL)
        OR (role IN ('org_admin','analyst','assessor','viewer') AND org_id IS NOT NULL))
);

-- ---------------------------------------------------------------- cross-framework mapping (global)

CREATE TABLE control_mappings (
    id               SERIAL PRIMARY KEY,
    source_req_id    INT NOT NULL REFERENCES requirements(id),
    target_req_id    INT NOT NULL REFERENCES requirements(id),
    relationship     TEXT NOT NULL CHECK (relationship IN ('equivalent','partial_overlap','related','independent')),
    justification    TEXT NOT NULL,
    method           TEXT NOT NULL DEFAULT 'manual' CHECK (method IN ('manual','rule','semantic','llm')),
    confidence       REAL,
    approval_status  TEXT NOT NULL DEFAULT 'pending' CHECK (approval_status IN ('pending','approved','rejected')),
    created_by       INT NOT NULL REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by       INT REFERENCES users(id),
    decided_at       TIMESTAMPTZ,
    decision_note    TEXT,
    CHECK (source_req_id <> target_req_id),
    UNIQUE (source_req_id, target_req_id)
);

-- ---------------------------------------------------------------- evidence and assessments (per organization)

CREATE TABLE evidence (
    id                SERIAL PRIMARY KEY,
    org_id            INT NOT NULL REFERENCES organizations(id),
    name              TEXT NOT NULL,
    description       TEXT,
    storage_path      TEXT,                     -- uploaded file, stored under its SHA-256
    original_filename TEXT,
    content_type      TEXT,
    size_bytes        BIGINT,
    sha256            CHAR(64),
    external_url      TEXT,
    owner_user_id     INT REFERENCES users(id),
    created_by        INT NOT NULL REFERENCES users(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    submitted_by      INT REFERENCES users(id),   -- whoever attached the current file/URL; cannot also accept it
    submitted_at      TIMESTAMPTZ,
    review_due_date   DATE,
    status            TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('not_requested','pending','submitted','under_review','accepted','rejected','expired')),
    reviewer_comments TEXT,
    CHECK (status IN ('not_requested','pending') OR storage_path IS NOT NULL OR external_url IS NOT NULL)
);
CREATE INDEX evidence_org_idx ON evidence (org_id, status);

CREATE TABLE evidence_requirements (
    evidence_id    INT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
    requirement_id INT NOT NULL REFERENCES requirements(id),
    PRIMARY KEY (evidence_id, requirement_id)
);

-- An assessment is append-only history; the latest row per (org, requirement) is the current result.
CREATE TABLE assessments (
    id                   SERIAL PRIMARY KEY,
    org_id               INT NOT NULL REFERENCES organizations(id),
    requirement_id       INT NOT NULL REFERENCES requirements(id),
    assessor_id          INT NOT NULL REFERENCES users(id),
    result               TEXT NOT NULL CHECK (result IN ('compliant','partially_compliant','non_compliant','not_applicable')),
    maturity_level       SMALLINT,               -- only for frameworks that define a maturity model
    notes                TEXT,
    assessed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    framework_version_id INT NOT NULL REFERENCES framework_versions(id)  -- exact version assessed against
);
CREATE INDEX assessments_org_req_idx ON assessments (org_id, requirement_id, assessed_at DESC);

CREATE TABLE assessment_evidence (
    assessment_id INT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    evidence_id   INT NOT NULL REFERENCES evidence(id),
    PRIMARY KEY (assessment_id, evidence_id)
);

CREATE TABLE audit_logs (
    id        BIGSERIAL PRIMARY KEY,
    org_id    INT,
    actor     TEXT,
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    detail    JSONB,
    at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
