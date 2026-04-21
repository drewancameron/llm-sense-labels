# Frozen example I/O per stage

These JSON files show exactly what the four pipeline stages consume and
produce. They exist so that a reader can understand the methodology
without running the pipeline (no API key, no database, no Perseus fetch).

Each stage is illustrated on the same anchor passage — *Iliad* 1.1–7, the
opening proem of the *Iliad* — because it is widely known, metrically
regular, and contains the ψυχή occurrence at line 3 (`πολλὰς δ᾽ ἰφθίμους
ψυχὰς Ἄιδι προΐαψεν`, "it sent many strong souls to Hades"). This
occurrence is the canonical Homeric sense of ψυχή — "shade of the dead"
— which differs sharply from the Classical "soul, self" sense, and is
exactly the kind of diachronic distinction the pipeline is designed to
capture.

| File | Stage | Purpose |
|---|---|---|
| `stage1_il1_psyche_input.json`  | 1 | What Stage 1 receives for this passage |
| `stage1_il1_psyche_output.json` | 1 | The context record it emits |
| `stage2_il1_psyche_input.json`  | 2 | Stage-2 prompt payload for the occurrence at Il.1.3 |
| `stage2_il1_psyche_output.json` | 2 | The candidate-label record it emits |
| `stage3_psyche_input.json`      | 3 | Aggregated Stage-2 candidates for ψυχή |
| `stage3_psyche_output.json`     | 3 | The canonical inventory Stage 3 produces |
| `stage4_batch_sample.json`      | 4 | A small batch of bulk classifications with the inventory applied |

The examples are **illustrative but realistic**: they mirror the JSON
shapes validated by the pydantic schemas in `scripts/lib/schemas.py` and
would survive a round-trip through `model_validate_json`.
