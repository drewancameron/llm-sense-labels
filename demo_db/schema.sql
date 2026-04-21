-- Demo database schema for the llm-sense-labels showcase pipeline.
--
-- Six logical groups of tables:
--
--   1. Corpus           — source texts and their metadata.
--   2. Lemmata          — the ten target headwords and their inflected forms.
--   3. Alignment        — Greek passages paired with English translations and notes.
--   4. Pipeline outputs — stage 1 context records, stage 2 candidate labels,
--                         stage 3 sense inventory, stage 4 bulk labels.
--   5. Batch bookkeeping — OpenAI batch job IDs and status, for reproducibility.
--   6. Provenance       — which LLM call produced which row.
--
-- The schema is deliberately flat and easy to inspect by hand. Stage scripts
-- write rows with full provenance so that any downstream result can be traced
-- back to the exact Greek passage, aligned English, note, and LLM call that
-- produced it.

PRAGMA foreign_keys = ON;

-- ─── 1. Corpus ──────────────────────────────────────────────────────

CREATE TABLE works (
    work_id         TEXT PRIMARY KEY,          -- e.g. 'tlg0003.tlg001.perseus-grc2'
    author          TEXT NOT NULL,             -- 'Thucydides'
    title           TEXT NOT NULL,             -- 'The History of the Peloponnesian War'
    date_estimate   TEXT,                      -- '431-411 BCE'
    period          TEXT,                      -- archaic | classical | hellenistic | imperial | koine | late_antique
    genre           TEXT,                      -- epic | lyric | tragic | comic | oratorical | historiographical | philosophical | scientific | religious_LXX | religious_NT
    language        TEXT DEFAULT 'grc',        -- 'grc' or 'eng' for translation-works
    license_status  TEXT NOT NULL,             -- 'public_domain' | 'cc_by' | 'cc_by_sa' | 'fair_use'
    source_url      TEXT,                      -- canonical URL at Perseus or Scaife
    notes           TEXT
);

CREATE TABLE passages (
    passage_id      TEXT PRIMARY KEY,          -- e.g. 'Thuc.1.1.1'
    work_id         TEXT NOT NULL REFERENCES works(work_id),
    reference       TEXT NOT NULL,             -- canonical reference string, human-facing
    sequence        INTEGER NOT NULL,          -- order within the work
    greek_text      TEXT NOT NULL,
    context_before  TEXT,                      -- preceding passage(s), concatenated
    context_after   TEXT,                      -- following passage(s), concatenated
    word_count      INTEGER
);

CREATE INDEX idx_passages_work ON passages(work_id);

-- ─── 2. Lemmata ─────────────────────────────────────────────────────

CREATE TABLE lemmata (
    slug            TEXT PRIMARY KEY,          -- 'logos'
    lemma_greek     TEXT NOT NULL,             -- 'λόγος'
    pie_root        TEXT,
    pie_gloss       TEXT,
    domain_primary  TEXT,
    domain_secondary_json TEXT,                -- JSON array
    expected_pattern TEXT,
    wiktionary_page TEXT,
    lsj_entry       TEXT
);

-- A many-to-many: a lemma has many inflected surface forms, and a surface form
-- can occasionally belong to more than one lemma (handled by morphology).
CREATE TABLE lemma_forms (
    lemma_slug      TEXT NOT NULL REFERENCES lemmata(slug),
    surface_form    TEXT NOT NULL,             -- exact form as it may appear in text
    surface_norm    TEXT NOT NULL,             -- lowercased, accent-stripped
    morph_tag       TEXT,                      -- e.g. 'n-s---mn-' (Perseus morphtag)
    PRIMARY KEY (lemma_slug, surface_form, morph_tag)
);

CREATE INDEX idx_lemma_forms_norm ON lemma_forms(surface_norm);

CREATE TABLE occurrences (
    occurrence_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    passage_id      TEXT NOT NULL REFERENCES passages(passage_id),
    lemma_slug      TEXT NOT NULL REFERENCES lemmata(slug),
    surface_form    TEXT NOT NULL,
    char_offset_start INTEGER,                 -- offset into passages.greek_text
    char_offset_end   INTEGER,
    morph_tag       TEXT,
    UNIQUE (passage_id, lemma_slug, char_offset_start)
);

CREATE INDEX idx_occurrences_lemma ON occurrences(lemma_slug);
CREATE INDEX idx_occurrences_passage ON occurrences(passage_id);

-- ─── 3. Alignment: translations and notes attached to passages ──────

CREATE TABLE translations (
    translation_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    passage_id      TEXT NOT NULL REFERENCES passages(passage_id),
    translator      TEXT NOT NULL,             -- 'Jowett' | 'Lattimore' | 'anonymous Perseus'
    translation_work_id TEXT REFERENCES works(work_id),  -- if the translation has its own work record
    aligned_text    TEXT NOT NULL,
    alignment_confidence REAL DEFAULT 1.0,     -- 0-1, from the alignment procedure
    license_status  TEXT NOT NULL
);

CREATE INDEX idx_translations_passage ON translations(passage_id);

CREATE TABLE notes (
    note_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    passage_id      TEXT NOT NULL REFERENCES passages(passage_id),
    translation_id  INTEGER REFERENCES translations(translation_id),  -- if note belongs to a specific translation
    note_author     TEXT,                      -- translator or commentator
    note_type       TEXT,                      -- 'footnote' | 'commentary' | 'lexical' | 'apparatus' | 'paratext'
    note_text       TEXT NOT NULL,
    license_status  TEXT NOT NULL
);

CREATE INDEX idx_notes_passage ON notes(passage_id);

-- ─── 4. Pipeline outputs ────────────────────────────────────────────

-- Stage 1: per-passage context record.
CREATE TABLE context_records (
    passage_id      TEXT PRIMARY KEY REFERENCES passages(passage_id),
    register_label  TEXT,
    register_confidence REAL,
    register_evidence TEXT,
    metre_label     TEXT,
    metrical_pressure TEXT,                    -- 'none' | 'low' | 'moderate' | 'high'
    metre_evidence  TEXT,
    source_nature   TEXT,
    source_confidence REAL,
    source_evidence TEXT,
    author_stance   TEXT,
    period_inferred TEXT,
    gold_notes_json TEXT,                      -- JSON array of {translator_or_commentator, type, target_lemma, quote, philological_weight}
    notes           TEXT,
    raw_response_json TEXT,                    -- full LLM response for traceability
    model_id        TEXT NOT NULL,
    batch_job_id    TEXT REFERENCES batch_jobs(batch_job_id),
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Stage 2: per-occurrence candidate sense labels.
CREATE TABLE candidate_labels (
    candidate_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    occurrence_id   INTEGER NOT NULL REFERENCES occurrences(occurrence_id),
    sense_label     TEXT NOT NULL,             -- inventory label or '__NEW__'
    proposed_gloss  TEXT,                      -- only if sense_label is '__NEW__'
    confidence      REAL NOT NULL,
    is_primary      INTEGER NOT NULL,          -- 0/1
    evidence_json   TEXT NOT NULL,             -- JSON array of evidence records
    register_override TEXT,
    metrical_constraint_acknowledged INTEGER DEFAULT 0,
    gold_note_relied_upon TEXT,
    notes           TEXT,
    extraction_confidence REAL,
    raw_response_json TEXT,
    model_id        TEXT NOT NULL,
    batch_job_id    TEXT REFERENCES batch_jobs(batch_job_id),
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_candidate_occurrence ON candidate_labels(occurrence_id);
CREATE INDEX idx_candidate_sense ON candidate_labels(sense_label);

-- Stage 3: the clean canonical sense inventory, per lemma.
CREATE TABLE sense_inventory (
    inventory_entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma_slug      TEXT NOT NULL REFERENCES lemmata(slug),
    sense_index     INTEGER NOT NULL,          -- 0-based ordering within the lemma's inventory
    label           TEXT NOT NULL,
    gloss           TEXT NOT NULL,
    confidence      TEXT NOT NULL,             -- 'established' | 'probable' | 'questionable'
    period_notes    TEXT,
    attested_periods_json TEXT,                -- JSON array of period tags
    register_affinities_json TEXT,             -- JSON array
    domain_affinities_json TEXT,               -- JSON array
    merged_candidate_ids_json TEXT,            -- JSON array of candidate_labels.candidate_id
    representative_evidence_json TEXT,
    notes           TEXT,
    raw_response_json TEXT,
    model_id        TEXT NOT NULL,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (lemma_slug, sense_index)
);

CREATE INDEX idx_inventory_lemma ON sense_inventory(lemma_slug);

-- Stage 4: per-occurrence bulk classification against the stable inventory.
CREATE TABLE bulk_labels (
    bulk_label_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    occurrence_id   INTEGER NOT NULL REFERENCES occurrences(occurrence_id),
    chosen_sense_label TEXT NOT NULL,          -- from inventory, or 'uncertain'
    chosen_sense_index INTEGER NOT NULL,       -- -1 if uncertain
    confidence      REAL NOT NULL,
    register_applied TEXT,
    reason          TEXT,
    fallback_sense_label TEXT,
    fallback_confidence REAL,
    raw_response_json TEXT,
    model_id        TEXT NOT NULL,
    batch_job_id    TEXT REFERENCES batch_jobs(batch_job_id),
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (occurrence_id)                     -- one bulk label per occurrence
);

CREATE INDEX idx_bulk_occurrence ON bulk_labels(occurrence_id);

-- ─── 5. Batch bookkeeping ───────────────────────────────────────────

CREATE TABLE batch_jobs (
    batch_job_id    TEXT PRIMARY KEY,          -- OpenAI batch ID, e.g. 'batch_abc123'
    stage           TEXT NOT NULL,             -- 'stage1' | 'stage2' | 'stage4'
    model_id        TEXT NOT NULL,
    status          TEXT NOT NULL,             -- 'submitted' | 'in_progress' | 'completed' | 'failed' | 'expired'
    submitted_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at    TEXT,
    input_file_path TEXT,
    output_file_path TEXT,
    cost_usd        REAL,
    cost_breakdown_json TEXT,                  -- {input_tokens, output_tokens, cached_input_tokens}
    notes           TEXT
);

-- ─── 6. Convenience views ───────────────────────────────────────────

-- Flattened per-occurrence view joining Greek passage, best English
-- translation, and Stage-1 context record. Useful for Stage-2 prompt
-- construction and for hand-inspection.
CREATE VIEW occurrence_context AS
SELECT
    o.occurrence_id,
    o.lemma_slug,
    l.lemma_greek,
    o.surface_form,
    p.passage_id,
    p.reference,
    p.greek_text,
    p.context_before,
    p.context_after,
    w.author,
    w.title,
    w.period,
    w.genre,
    (SELECT aligned_text FROM translations t
        WHERE t.passage_id = p.passage_id
        ORDER BY alignment_confidence DESC LIMIT 1) AS best_translation,
    (SELECT translator FROM translations t
        WHERE t.passage_id = p.passage_id
        ORDER BY alignment_confidence DESC LIMIT 1) AS best_translator,
    cr.register_label,
    cr.metre_label,
    cr.source_nature
FROM occurrences o
JOIN lemmata l   ON o.lemma_slug = l.slug
JOIN passages p  ON o.passage_id = p.passage_id
JOIN works w     ON p.work_id = w.work_id
LEFT JOIN context_records cr ON p.passage_id = cr.passage_id;
