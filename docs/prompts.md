# Prompt design

The four stage prompts in `config/prompts/` are the scientific core of
the repository. This note explains the design choices in each.

## General principles

- **Markdown format, split at `## User template`.** Every prompt file has
  two halves. The system half defines the model's role and the rules.
  The user half is a template with `{placeholder}` fields filled in by
  the stage script from the database row.
- **Structured JSON output, validated.** Every stage emits a JSON
  object that is validated by a pydantic model in
  `scripts/lib/schemas.py`. Responses that fail validation are
  quarantined, not silently dropped, so prompt or model drift is
  visible.
- **Temperature 0.0 everywhere.** We want deterministic, auditable
  outputs, not creative paraphrase.
- **`response_format: {"type": "json_object"}` everywhere** to guarantee
  the model emits exactly one JSON object per call.

## Stage 1 — Context analysis

**File:** `config/prompts/stage1_context.md`
**Model:** `gpt-5.4` (premium override to `gpt-5.4-pro` on verse)

Four design choices worth highlighting:

1. **"You do not analyse the target lemma's sense here."** The prompt
   is explicit that sense-selection is out of scope for Stage 1. This
   keeps the context record an independent input to Stage 2, rather than
   a leaky partial classification.
2. **Metrical pressure as a distinct field.** Scansion is reasoning-
   hard; we ask for *both* the metrical scheme and an ordinal pressure
   rating (none / low / moderate / high). Downstream stages read
   `metrical_pressure` and can discount sense evidence when it is
   `high` (metre may have forced the word, not the author's semantic
   preference).
3. **Gold notes are rare and precious.** Three safeguards: (a) the
   list of permitted types is narrow and well-defined, (b) explicit
   instruction "A gold note explicitly discusses the *word* or its
   *senses* — not the referent", and (c) `philological_weight` forces
   the model to rank its own finds rather than treating every note as
   equally important.
4. **Uncertainty is a first-class answer.** Every sub-field admits
   `uncertain`. The downstream stages cope with `register: uncertain`
   cleanly (they simply don't use it as a tiebreaker). This is better
   than forcing a confident-looking wrong label.

## Stage 2 — Per-occurrence sense extraction

**File:** `config/prompts/stage2_extraction.md`
**Model:** `gpt-5.4`

1. **Evidence before label.** The prompt instructs the model to start
   from the English translation and work back to the Greek. The
   translation is the observable datum; the Greek is what is being
   classified. This inverts the usual direction and produces more
   traceable labels.
2. **Confidence calibration ladder.** A five-rung ladder maps
   confidence ranges to evidence types (0.9+ = direct rendering with
   corroborating note; 0.3-0.5 = inferential only). Without this ladder
   the model would default to 0.8-0.9 for everything.
3. **Translator disagreement is a signal.** The prompt names this
   explicitly. When two translators disagree, the model emits both as
   candidates with proportional confidence rather than picking one.
4. **"__NEW__" as an escape hatch.** If the evidence genuinely does
   not fit any seeded or inventory sense, the model emits a candidate
   with `sense_label: "__NEW__"` and a proposed gloss. Stage 3 decides
   whether to admit it. This is the mechanism by which the inventory
   grows from real evidence rather than prior assumption.
5. **The Stage 1 record rides in as structured JSON.** Rather than
   being embedded in prose, it is pasted as a JSON code block in the
   user prompt. This gives the model a cleanly parseable context
   signal.

## Stage 3 — Philological inventory synthesis

**File:** `config/prompts/stage3_inventory.md`
**Model:** `gpt-5.4-pro`

1. **Two distinct duties: merge and separate.** The prompt names both.
   Under-merging leaves a noisy inventory of near-duplicates; over-
   merging erases real distinctions. The prompt gives concrete examples
   of each.
2. **Weight by evidence, not by count.** Without this instruction, the
   model will prefer the sense with the most candidates. With it, the
   model treats a single gold polysemy note as stronger than ten
   paraphrased renderings — which is philologically correct.
3. **Absences and additions are first-class outputs.** Two dedicated
   fields, `senses_in_reference_but_unsupported` and
   `senses_beyond_reference`, make the comparison to LSJ / Wiktionary
   explicit. Corpus coverage gaps are then visible, not hidden.
4. **Period notes are mandatory.** Every canonical sense must declare
   which periods it is attested in. This is what the downstream
   diachronic analysis uses.
5. **Realtime, not batch.** Only ~10 calls in a full run, and each one
   has a large input (tens of thousands of tokens of candidate
   evidence). Batch latency is not worth the 50% discount here.

## Stage 4 — Bulk classification

**File:** `config/prompts/stage4_bulk.md`
**Model:** `gpt-5.4-mini`

1. **Fast and direct.** Shortest of the four prompts. The model picks
   an index into a numbered inventory and justifies briefly.
2. **"Uncertain" is a permitted answer.** Occurrences that do not fit
   the inventory are flagged rather than coerced. Downstream workflows
   can re-inspect them or route them back to Stage 2 with higher-tier
   model.
3. **Register and metre are explicit tiebreakers.** The prompt names
   this behaviour: when two senses are close, the Stage 1 context
   record should tip the decision. This operationalises the philological
   principle that sense selection is context-sensitive, not just
   dictionary-matched.
4. **No new senses.** Unlike Stage 2, Stage 4 cannot propose
   `__NEW__`. The inventory is fixed by the time this stage runs; its
   job is classification, not discovery.

## Prompt caching

Every stage uses server-side prompt caching on OpenAI's side for the
**system prompt**. Because the system prompt is byte-identical across
all requests within a stage, OpenAI's cache hits after the first call
and charges the cached-input rate (for `gpt-5.4`, $0.25 per 1M tokens
instead of $2.50 — a 90% discount). The wrapper in
`scripts/lib/openai_batch.py` makes no explicit cache call; it simply
keeps the system prompt stable so the cache hits automatically.

A user who wants to modify a prompt mid-run should bear in mind that
each edit invalidates the cache and the next batch of calls will pay
the full input price until the new prompt is reused enough to warm the
cache again.
