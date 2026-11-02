-- AI-assisted mapping suggestions. A suggestion is only a candidate pair: it never becomes a mapping until a
-- reviewer proposes it (with a justification they own) and a second reviewer approves it.
CREATE TABLE IF NOT EXISTS mapping_suggestions (
    id                 SERIAL PRIMARY KEY,
    source_req_id      INT NOT NULL REFERENCES requirements(id),
    target_req_id      INT NOT NULL REFERENCES requirements(id),
    lexical_score      REAL NOT NULL,
    source_text_sha    TEXT NOT NULL,          -- texts as they were when scored; a changed text makes the row stale
    target_text_sha    TEXT NOT NULL,
    llm_model          TEXT,
    prompt_version     TEXT,
    llm_relationship   TEXT CHECK (llm_relationship IN ('equivalent','partial_overlap','related','independent')),
    llm_rationale      TEXT,
    llm_source_quote   TEXT,
    llm_target_quote   TEXT,
    llm_verified       BOOLEAN,                -- both quotes were found verbatim in the real texts
    status             TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','proposed','dismissed')),
    decided_by         INT REFERENCES users(id),
    decided_at         TIMESTAMPTZ,
    note               TEXT,
    mapping_id         INT REFERENCES control_mappings(id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (source_req_id <> target_req_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS mapping_suggestions_pair
    ON mapping_suggestions (LEAST(source_req_id, target_req_id), GREATEST(source_req_id, target_req_id));
CREATE INDEX IF NOT EXISTS mapping_suggestions_status ON mapping_suggestions (status, lexical_score DESC);
