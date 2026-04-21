# Data sources and attribution

## Greek texts

All Greek source texts in the demo corpus are drawn from the
[Perseus Digital Library](http://www.perseus.tufts.edu/), via the
[PerseusDL/canonical-greekLit](https://github.com/PerseusDL/canonical-greekLit)
GitHub mirror. Each TEI XML file is fetched on first run of
`demo_db/build_demo_db.py` and cached under `demo_db/cache/`.

The five curated works, with identifiers and licences:

| Work | Perseus ID | Licence |
|---|---|---|
| Homer, *Iliad* Book 1 | `tlg0012.tlg001.perseus-grc2` | CC-BY-SA 3.0 |
| Sophocles, *Antigone* | `tlg0011.tlg002.perseus-grc2` | CC-BY-SA 3.0 |
| Herodotus, *Histories* Book 1 | `tlg0016.tlg001.perseus-grc2` | CC-BY-SA 3.0 |
| Plato, *Apology* | `tlg0059.tlg002.perseus-grc2` | CC-BY-SA 3.0 |
| Anonymous, *Gospel of Matthew* | `tlg0031.tlg001.perseus-grc2` | CC-BY-SA 3.0 |

Original compositions predate 1925 by millennia; the TEI encoding is
what Perseus releases under CC-BY-SA. Attribution to the Perseus Digital
Library is preserved in the `works.source_url` column of the demo
database for every passage.

## English translations

All English translations are in the public domain in the United States
and the United Kingdom (authors died before 1955 and originals published
before 1925):

| Translator | Work | Date |
|---|---|---|
| Samuel Butler | *Iliad* | 1898 |
| Francis Storr | *Antigone* | 1912 |
| A. D. Godley | Herodotus *Histories* | 1920 |
| Benjamin Jowett | Plato *Apology* | 1871 |

Translations are fetched from the same PerseusDL GitHub mirror, from
the `perseus-eng*` TEI files that accompany the Greek.

## Wiktionary forms and senses

`config/wiktionary_forms.json` contains inflected forms and sense
definitions for the 10 target lemmata, extracted from the English
Wiktionary via the parent DiachronicSenseChange project. Wiktionary
content is licensed CC-BY-SA 4.0; see
[Wiktionary copyright policy](https://en.wiktionary.org/wiki/Wiktionary:Copyrights).

## Reference sense inventory

`outputs/reference_sense_inventory.json` is a derived work: a curated
set of canonical senses for the 10 target lemmata produced by the
parent DiachronicSenseChange project using an earlier version of this
pipeline (GPT-4.1 for synthesis) followed by human review. It is shipped
as a "what good looks like" benchmark for readers to compare against
their own Stage 3 output. Licence: CC-BY-SA 4.0 (as a derivative of
Wiktionary sense data).

## What is *not* in this repository

- **No modern copyrighted translations.** The parent research project
  uses a range of modern translations (Lattimore, Fagles, West,
  Stallings, and others) under fair-use provisions for internal research
  supervision. None of those translations is redistributed here. The
  pipeline code and prompts work identically with any translation a user
  chooses to ingest; the demo simply uses the public-domain ones.
- **No commercial commentaries or Loeb content.** Loeb Classical Library
  volumes are *not* public domain in most jurisdictions; no Loeb text
  is ingested, quoted, or cached in this repository.
- **No Atticist lexica or modern scholarship PDFs.** The parent project
  references philological monographs (Bru 2022 on Atticist lexica,
  Luraghi & Mertyris 2022 on valency change, Georgakopoulos & Polis
  2019 on semantic maps) for methodological guidance. Those texts are
  not redistributed here; references live in the accompanying
  publication, not in the repository.

## Citation

If you use this code or methodology in a publication, please cite:

- The Perseus Digital Library (for the Greek texts and translations)
- Wiktionary (for the morphological forms and baseline senses)
- This repository by URL

The parent research project (DiachronicSenseChange) will publish its
methodology separately; a link to that publication will be added here
when it is available.
