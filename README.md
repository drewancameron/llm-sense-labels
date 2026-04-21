# llm-sense-labels

Constructing sense inventories and labelled occurrences for Ancient Greek lemmata, using LLM-assisted reading of paired Greek–English corpora, translators' notes, and scholarly commentary.

This is a showcase of the *methodology*: how to distil scholarship into machine-readable supervision for diachronic sense change research, without hand-annotating thousands of passages.

## What this demonstrates

1. **Automatic sense-label construction** from paired Greek–English aligned texts. An LLM reads a Greek passage alongside its published English translation and emits a structured record of which sense of the target lemma is instantiated.
2. **Context-aware enrichment** using translators' notes and source metadata. A dedicated upstream pass establishes register (epic, historiography, oratory, …), metre (dactylic hexameter, iambic trimeter, prose, …), source nature (original / archaising / parodic / koine), and mines translators' notes for explicit discussion of polysemy or diachronic shift. These metadata constrain downstream sense selection: mock-archaic register biases toward older senses, metrical pressure can force non-standard word choices, and translator metacommentary is the philological gold standard.

## The four-stage pipeline

| Stage | Script | Model | Purpose |
|---|---|---|---|
| 1 | `scripts/stage1_analyze_context.py` | `gpt-5.4` | Per-passage register / metre / source-type analysis + translator-note mining |
| 2 | `scripts/stage2_extract_evidence.py` | `gpt-5.4` | Per-occurrence deep sense extraction from Greek + English + notes + Stage-1 context |
| 3 | `scripts/stage3_build_inventory.py` | `gpt-5.4-pro` | Philological synthesis of candidate senses → clean per-lemma inventory |
| 4 | `scripts/stage4_bulk_classify.py` | `gpt-5.4-mini` | Bulk classification against the stable inventory |

Batch API and prompt caching are enabled by default; see `docs/cost_estimates.md` for cost numbers.

## Ten worked lemmata

λόγος, κόσμος, ψυχή, νόμος, δίκη, ἀρετή, φύσις, τέχνη, θεός, σῶμα. These span the institutional / cosmological / ethical / religious / concrete domains where diachronic sense change is most visible.

## Quickstart

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
python demo_db/build_demo_db.py                           # fetch Perseus XML + build demo.db
python scripts/stage1_analyze_context.py --lemma logos    # context records
python scripts/stage2_extract_evidence.py --lemma logos   # candidate labels
python scripts/stage3_build_inventory.py --lemma logos    # clean sense inventory
python scripts/stage4_bulk_classify.py --lemma logos      # classify remaining occurrences
```

Frozen example I/O per stage lives in `examples/` for readers who don't want to run anything.

## Repository layout

```
config/              10 target lemmata, model tier mapping, prompt templates
demo_db/             schema + builder for a small public-domain SQLite corpus
scripts/             four pipeline stages + ingest + shared library
examples/            frozen input/output JSON samples per stage
outputs/             where pipeline writes; includes curated reference inventory
docs/                methodology, prompts, cost estimates, data sources
```

## What this is not

- **Not a production research tool.** For that, see the private parent project.
- **Not an evaluation benchmark.** Labels come from an LLM, not gold standard annotation.
- **Not redistributing copyrighted translations.** The demo corpus is built from public-domain Perseus TEI only. Any commercial translation or modern commentary used in the parent research was read by the pipeline under fair use and never redistributed.

## Data and copyright

All demo corpus data is public domain via [Perseus Digital Library](http://www.perseus.tufts.edu/). Wiktionary sense definitions are CC-BY-SA. See `docs/data_sources.md`.

## License

MIT. See `LICENSE`.
