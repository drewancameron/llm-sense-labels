# Demo database

A small SQLite corpus used by the four-stage pipeline, built entirely
from public-domain Perseus TEI.

## Contents

Five curated source works spanning the register / genre / period
spectrum the pipeline is designed to handle:

| Author | Work | Period | Genre | Translator (English) |
|---|---|---|---|---|
| Homer | *Iliad*, Book 1 | archaic | epic | Samuel Butler (1898) |
| Sophocles | *Antigone* | classical | tragic | Francis Storr (1912) |
| Herodotus | *Histories*, Book 1 | classical | historiographical | A. D. Godley (1920) |
| Plato | *Apology* | classical | philosophical | Benjamin Jowett (1871) |
| Anonymous | Gospel of Matthew, Ch. 5–7 | koine | religious (NT) | (Perseus-bundled) |

The Greek texts are CC-BY-SA via Perseus; the English translations are
out of copyright (all pre-1925).

## Building

```bash
python demo_db/build_demo_db.py
```

Runtime 10–30 seconds. Fetched TEI is cached under `demo_db/cache/`;
the cache is gitignored.

## Schema

See `schema.sql`. Five logical groups:

- **Corpus** — works, passages
- **Lemmata** — the 10 target headwords and their Wiktionary-sourced inflected forms
- **Alignment** — translations and notes attached to passages
- **Pipeline outputs** — context_records, candidate_labels, sense_inventory, bulk_labels
- **Batch bookkeeping** — batch_jobs (OpenAI job IDs and status)

A convenience view `occurrence_context` flattens passage + best
translation + Stage-1 context for quick inspection:

```sql
SELECT reference, surface_form, register_label, metre_label, best_translation
FROM occurrence_context
WHERE lemma_slug = 'logos'
LIMIT 10;
```

## Caveats

- **Occurrence matching is surface-level**, using accent-stripped form
  matching against a Wiktionary forms list. A form that belongs to two
  lemmata produces two occurrence rows. Morphological disambiguation is
  out of scope for the demo.
- **Passage granularity is TEI-driven**: one `<p>` or `<l>` per passage.
  For Herodotus and Plato this produces paragraphs; for Homer and
  Sophocles it produces single verse lines. The pipeline copes with both.
- **English alignment is ref-based**: we match `passage.reference`
  against references in the English TEI. When the English text is
  coarser-grained (e.g. English paragraph covers three Greek sections),
  we fall back to prefix matching with reduced confidence.
