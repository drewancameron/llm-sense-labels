# Methodology

## The problem

Historical-semantic research on Ancient Greek needs labelled occurrences
at a scale no human team can annotate by hand. A single content lemma
like λόγος has tens of thousands of occurrences across the surviving
corpus, and *each one* needs a sense tag before any diachronic modelling
can begin. The traditional alternatives — small hand-annotated samples
or dictionary-based heuristics — are either too small to support
statistical claims or too crude to distinguish genuine polysemy from
contextual flexibility.

The labels are *already there* — latent in two centuries of translations,
translator's notes, and scholarly commentary. Translators read the
Greek, settle on an English rendering, and occasionally leave notes
explaining their choice. That act of translation is, implicitly, a sense
disambiguation. If we can read translations and notes carefully enough,
we can recover the senses they encode, without ever asking a human to
re-annotate the Greek.

This pipeline does that reading, with four LLM passes of increasing
specificity.

## The four stages

### Stage 1 — Context analysis (per passage)

Before any sense extraction, we establish the philological conditions
under which a sense will be selected. For each passage that contains a
target lemma, `stage1_analyze_context.py` asks **gpt-5.4** for four
things:

1. **Register** — epic, historiographical, philosophical, oratorical,
   religious (LXX or NT), scientific, documentary, or poetic subgenres
   (lyric, tragic, comic). Register biases which senses are available:
   a Second-Sophistic author atticising Classical style will prefer
   Classical senses over contemporary ones.
2. **Metre** — prose or verse, and if verse, which scheme. Metrical
   pressure can force lexical choices that would otherwise be unnatural
   (a poet may use an archaising sense because its syllabic shape fits
   a metrical slot). Flagging this prevents the pipeline from treating
   such cases as evidence of live semantic variation.
3. **Source nature** — original composition, archaising imitation,
   parody, direct quotation, koine vernacular, translation from Semitic
   (LXX). These are orthogonal to register and change how sense
   evidence should be weighted.
4. **Gold notes** — any translator's note that explicitly discusses
   polysemy, semantic shift, metrical constraint, etymology, or the
   translator's lexical choice. These are rare but disproportionately
   valuable: they function as human-certified supervision for the
   sense-extraction pass that follows. The prompt includes strong
   instructions against inflating ordinary footnotes into gold notes.

Output is a structured JSON record (see `examples/stage1_il1_psyche_output.json`).

### Stage 2 — Per-occurrence sense extraction

For each occurrence of a target lemma, `stage2_extract_evidence.py`
builds a prompt containing:

- The Greek passage and its immediate context window
- Every aligned English translation available
- Every translator / commentator note attached to the passage
- The Stage 1 context record (as structured input)
- The current sense inventory, or seed senses from `config/lemmata.yaml`
  during the inventory-building phase

**gpt-5.4** then reads all of this and emits a candidate-sense record:
which sense(s) of the lemma are instantiated, with exact quoted evidence
for each. Crucially, the model is instructed to work *from the
translation back to the Greek*, not the other way round. The translation
is the observable datum; the Greek is what is being classified.

Three design choices in this stage do scientific work:

- **Evidence quotation is mandatory.** Every sense claim must quote the
  English span that supports it. This pins the pipeline to what the
  translator actually wrote, rather than what the model believes the
  Greek means.
- **Translator disagreement is a signal.** When two translators render
  the same lemma differently, the model records both candidates with
  proportional confidence. Disagreement is information about ambiguity.
- **Uncertainty is a permitted answer.** The confidence calibration
  ladder in the system prompt keeps the model from over-committing.
  Confidence < 0.5 occurrences are flagged and filtered out of training
  signal downstream.

### Stage 3 — Philological inventory synthesis (per lemma)

After Stage 2 has produced many candidate labels for a lemma, most of
which overlap and none of which alone constitute a clean inventory, we
need to synthesise. `stage3_build_inventory.py` gathers all candidate
labels for a lemma, plus scholarly reference material (Wiktionary
senses, PIE root, gold notes surfaced in Stage 1) and asks
**gpt-5.4-pro** to emit a canonical sense inventory.

The model is instructed to:

- Merge near-duplicates (e.g. "speech", "spoken word", "utterance" →
  one canonical sense)
- Separate genuine distinctions ("ratio, proportion" vs. "discourse")
- Weight by evidence, not by count (one gold polysemy note outranks ten
  shallow renderings)
- Record period notes for each sense based on the dates of supporting
  passages
- Flag *absences* (senses in LSJ not seen in the corpus) and *additions*
  (senses in the corpus not in LSJ)

This is a reasoning-heavy, low-volume stage — roughly one call per
lemma. We use the premium tier because the output becomes the anchor
for Stage 4 on thousands of occurrences.

### Stage 4 — Bulk classification (per occurrence)

With the inventory stable, the remaining occurrences can be classified
cheaply. `stage4_bulk_classify.py` asks **gpt-5.4-mini** to pick the
best-fitting sense from the inventory for each occurrence, with the
Stage 1 context record as a side-channel signal. This is where the
investment in register and metre pays off: when two senses are close,
the register / metre marking tips the decision.

Uncertain cases (`chosen_sense_label: "uncertain"`) are preserved rather
than forced into a misfitting bucket. These are the occurrences a
downstream researcher should re-inspect.

## Why four stages and not one

An end-to-end prompt — "here is a Greek passage and its translation,
give me the sense of λόγος" — would work for the easy cases. It would
systematically mis-handle the hard cases, in exactly the ways a careful
philologist would spot:

- **Atticising authors** using archaic senses would be assigned the
  contemporary sense.
- **Metrically constrained verse** would be read as if lexical choice
  were free.
- **Koine register shifts** (νόμος as "Torah", ψυχή as "life", δίκαιος
  as "righteous") would be silently flattened into their Classical
  senses.
- **Translator notes** — the most decisive evidence in the entire
  corpus — would be ignored or weighted incorrectly because they are a
  different kind of evidence from the body translation.

Separating context analysis from sense extraction lets each step use
its own prompt, its own model tier, and its own cost profile, while
ensuring the right structural signals reach the decision point. The
cost is more engineering; the benefit is labels that respect the
philological character of the corpus.

## What the pipeline does *not* do

- **It does not invent evidence.** Every sense claim is anchored to a
  quoted span from a translation or note. If there is no evidence, the
  candidate is emitted with low confidence or marked uncertain.
- **It is not a morphological analyser.** Occurrence matching is
  surface-level (accent-stripped form comparison). A production
  deployment should add Stanza / CLTK for morphological disambiguation.
- **It is not a gold standard.** Labels come from an LLM reading
  scholarship. The intended use is high-volume supervision for a
  downstream model that can be independently evaluated on a smaller
  hand-annotated test set.
- **It does not redistribute copyrighted translations.** The demo corpus
  is built from public-domain Perseus TEI only. The parent research
  project uses modern translations under fair-use provisions and never
  redistributes them; this showcase repository observes the same limit.
