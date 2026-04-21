"""Pydantic models for the four pipeline-stage outputs.

These schemas double as (a) the structure the LLM is asked to emit, and
(b) the validation layer that checks the LLM's response before it is
written to the database. When a response fails validation it is quarantined
to outputs/quarantine/ rather than silently dropped, so that prompt or
model drift is visible.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


# ─── Stage 1: context record ────────────────────────────────────────

class RegisterField(BaseModel):
    label: Literal[
        "epic", "lyric", "tragic", "comic", "oratorical",
        "historiographical", "philosophical", "scientific_technical",
        "religious_LXX", "religious_NT", "documentary", "uncertain",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str


class MetreField(BaseModel):
    label: Literal[
        "prose", "dactylic_hexameter", "iambic_trimeter",
        "trochaic_tetrameter", "elegiac_couplet", "lyric_mixed",
        "anapaestic", "choliambic", "uncertain",
    ]
    metrical_pressure: Literal["none", "low", "moderate", "high"]
    evidence: str


class SourceNatureField(BaseModel):
    label: Literal[
        "original", "archaising", "atticising", "homericising",
        "mock_archaic", "parodic", "quoted", "koine_vernacular",
        "translation_from_semitic", "uncertain",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str


class GoldNote(BaseModel):
    translator_or_commentator: str
    type: Literal[
        "polysemy_discussion", "diachronic_remark", "register_remark",
        "metrical_remark", "etymology_remark", "translator_choice_justification",
    ]
    target_lemma: str
    quote: str
    philological_weight: Literal["high", "moderate", "low"]


class ContextRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    passage_id: str
    register_info: RegisterField = Field(alias="register")
    metre: MetreField
    source_nature: SourceNatureField
    author_stance: str | None = None
    gold_notes: list[GoldNote] = Field(default_factory=list)
    period_inferred: Literal[
        "archaic", "classical", "hellenistic", "imperial",
        "koine", "late_antique", "uncertain",
    ]
    notes: str = ""


# ─── Stage 2: candidate sense label ─────────────────────────────────

class Evidence(BaseModel):
    evidence_text: str
    evidence_type: Literal[
        "translation_rendering", "translator_note",
        "commentary_discussion", "lexicon_gloss", "contextual_inference",
    ]
    source_identity: str
    directness: Literal["direct", "inferential", "contextual"]


class CandidateSense(BaseModel):
    sense_label: str
    sense_label_source: Literal["inventory", "__NEW__"]
    proposed_gloss: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    is_primary: bool
    evidence: list[Evidence]


class ContextRecordSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    register_info: str = Field(alias="register")
    metre: str
    source_nature: str
    gold_notes_attached: int


class CandidateLabel(BaseModel):
    occurrence_id: str
    passage_reference: str
    lemma: str
    lemma_slug: str
    surface_form: str
    context_record_summary: ContextRecordSummary
    candidate_senses: list[CandidateSense]
    register_override: str | None = None
    metrical_constraint_acknowledged: bool = False
    gold_note_relied_upon: str | None = None
    notes: str = ""
    extraction_confidence: float = Field(ge=0.0, le=1.0)


# ─── Stage 3: inventory entry ───────────────────────────────────────

class RepresentativeEvidence(BaseModel):
    candidate_id: int
    evidence_quote: str
    source: str


class CanonicalSense(BaseModel):
    label: str
    gloss: str
    confidence: Literal["established", "probable", "questionable"]
    period_notes: str
    attested_periods: list[str]
    merged_candidate_ids: list[int]
    register_affinities: list[str] = Field(default_factory=list)
    domain_affinities: list[str] = Field(default_factory=list)
    representative_evidence: list[RepresentativeEvidence]
    notes: str = ""


class UnsupportedReferenceSense(BaseModel):
    reference_sense: str
    reason: str


class SenseBeyondReference(BaseModel):
    proposed_label: str
    rationale: str


class SenseInventoryResponse(BaseModel):
    lemma: str
    lemma_slug: str
    canonical_senses: list[CanonicalSense]
    senses_in_reference_but_unsupported: list[UnsupportedReferenceSense] = Field(default_factory=list)
    senses_beyond_reference: list[SenseBeyondReference] = Field(default_factory=list)
    inventory_notes: str = ""


# ─── Stage 4: bulk label ────────────────────────────────────────────

class BulkLabel(BaseModel):
    occurrence_id: str
    lemma: str
    chosen_sense_label: str
    chosen_sense_index: int
    confidence: float = Field(ge=0.0, le=1.0)
    register_applied: str | None = None
    reason: str
    fallback_sense_label: str | None = None
    fallback_confidence: float | None = None
