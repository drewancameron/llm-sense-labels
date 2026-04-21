# Cost estimates

## OpenAI pricing (per 1M tokens, as of April 2026)

| Model | Input | Output | Cached input |
|---|---|---|---|
| gpt-5.4-pro  | $30.00 | $180.00 | — |
| gpt-5.4      |  $2.50 |  $15.00 | $0.25 |
| gpt-5.4-mini |  $0.75 |   $4.50 | — |
| gpt-5.4-nano |  $0.20 |   $1.25 | — |
| gpt-4.1      |  $2.00 |   $8.00 | — |
| gpt-4.1-mini |  $0.40 |   $1.60 | — |
| gpt-4.1-nano |  $0.10 |   $0.40 | — |

**Batch API** discounts both input and output by 50% (24-hour
turnaround). **Prompt caching** on `gpt-5.4` drops the cached portion to
$0.25 / 1M — a 90% discount that matters for the system prompt, which
is long and reused across every call within a stage.

## Volume assumptions for two run scales

| | Demo (one pass, 10 lemmata) | Full research run |
|---|---|---|
| Stage 1 passages | ~1,000 | ~5,000 |
| Stage 2 occurrences (deep) | ~500 | ~5,000 |
| Stage 3 lemmata | 10 | 10 |
| Stage 4 occurrences (bulk) | ~1,500 | ~15,000 |

Token-per-call averages used for the estimate:

- Stage 1 ≈ 3.5K in / 600 out
- Stage 2 ≈ 4K in / 300 out
- Stage 3 ≈ 20–25K in / 1.5–2K out
- Stage 4 ≈ 2K in / 100 out

## Estimated costs (recommended tier, with discounts)

| Stage | Model | Demo | Full |
|---|---|---:|---:|
| 1 Context analysis | gpt-5.4 | $17.75 | $88.75 |
| 2 Sense extraction | gpt-5.4 | $7.25 | $72.50 |
| 3 Inventory build  | gpt-5.4-pro | $0.73 | $0.93 |
| 4 Bulk classify    | gpt-5.4-mini | $2.93 | $29.25 |
| **Total, no discounts** | | **~$29** | **~$191** |
| **+ Batch API (50% off)** | | ~$14 | ~$96 |
| **+ Batch + prompt caching** | | **~$10** | **~$65** |

These are order-of-magnitude figures; actual costs will vary with the
corpus chosen and the model's answer length. The stage scripts report
actual cost at the end of each run based on `usage` fields from the API.

## Cost ceilings

`config/models.yaml` sets a per-stage cost ceiling:

```yaml
cost_ceilings:
  stage1_analyze_context: 50.00
  stage2_extract_evidence: 100.00
  stage3_build_inventory: 20.00
  stage4_bulk_classify: 50.00
```

The stage scripts print an estimated cost before any API call and
refuse to proceed if the estimate exceeds the ceiling. Override with
`--confirm-cost` when you know what you are doing.

## Premium vs. standard gpt-5.4

The recommended tier uses `gpt-5.4-pro` only for Stage 3 (philological
synthesis, 10 calls per full run, low marginal cost). A user who wants
maximum quality on Stage 1 and Stage 2 can swap to pro:

| Tier | Demo | Full |
|---|---:|---:|
| Recommended (5.4-pro Stage 3 only) | ~$29 | ~$191 |
| All-pro deep stages (pro Stages 1-3) | ~$312 | ~$1,550 |

The ~11× multiplier is worth paying *only* on workloads where 5.4-
standard produces measurable errors on hard cases. A smarter policy is
to route specific hard passages (verse with `metrical_pressure: high`,
passages with gold notes, ambiguous-register attestations) to pro and
keep the default on standard. That adds perhaps $30–80 to a full run
while preserving frontier-tier quality where it matters.

## Batch vs. realtime

All stage scripts default to the Batch API (50% discount, 24-hour SLA).
Pass `--realtime` to any stage script to swap to `chat.completions.
create` for quick iteration on small samples:

```bash
python scripts/stage2_extract_evidence.py --lemma logos --limit 3 --realtime
```

Stage 3 always runs realtime because it has ~10 calls per full run and
wants low latency.

## What the cost does *not* buy

- The cost covers inference only. It does not cover the human time to
  curate the 10 target lemmata, design the prompts, review the Stage 3
  inventories, or audit the Stage 4 classifications.
- No training of a classifier on the labels. That is downstream work.
- No hand-annotated evaluation set. That is mandatory for validating
  the labels; budget for it separately.
