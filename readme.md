# Fly Fairly — Airport Search Engine

Typo-tolerant, multi-script airport search. A Go API in front of a Meilisearch index,
seeded with OurAirports data enriched offline (aliases + translations + popularity) via
AWS Bedrock / Claude. React UI on top.

## Working Demo https://65-2-170-26.sslip.io/

## Architecture

```
React (Vite)  ──►  Go API  ──►  Meilisearch
                  /api/search       ▲
                                    │ seed (docs + ranking rules)
   Python pipeline (OurAirports CSV ──► Bedrock/Claude ──► enriched JSON)
```

| Component | Stack | Path |
|---|---|---|
| Search API | Go 1.26, net/http | [backend/](backend/) |
| Index | Meilisearch | container |
| Data pipeline | Python 3.14, boto3, pandas | [data/pipeline/enrich.py](data/pipeline/enrich.py) |
| UI | React 19 + Vite | [frontend/](frontend/) |

# Approach Memo

**Stack:** Go API + Meilisearch + React/Vite, Python enrichment pipeline on AWS Bedrock Agent
(Claude Sonnet 3.7), wired with Docker Compose.

**The core bet: do the hard linguistic work offline, keep the runtime a dumb fast lookup.** Aliases
and multi-script translations are pre-computed per airport once and folded into searchable fields, so
a typo-tolerant index does all the matching. The expensive, non-deterministic LLM is paid for once per
airport, never per search — latency stays in single-digit ms with no runtime model dependency.

## The idea we built on

**Airport search is entity resolution, not string matching.** A user types `JFK`, `New York`, `紐約`,
`Nueva York`, or `JFKK` — many surface forms, one entity. Naive single-field fuzzy matching fails the
brief's cases (`Florida`→`La Florida`, `Bali`→`Balikpapan`) because it optimizes string proximity when
the real target is *intent*.

You can close that gap at **query time** (expand every keystroke — powerful but slow, costly,
non-deterministic, hard to test) or at **index time** (enrich the document so it already contains every
form a user might type, making matching a trivial lookup). **I chose index-time enrichment:** each
airport is a "fat" record carrying its IATA code, city, country, aliases, and name across 11 languages.
Once the document says "Tokyo," "東京," "Haneda," and "HND," the runtime needs no cleverness.

This is the standard place-search playbook — denormalized documents, alias/synonym expansion,
multilingual names, and a popularity prior so the busy hub wins ties. A mature version is a curated
place graph; **I approximated it with LLM-generated enrichment** instead of licensing a dataset. **The
LLM is a one-time knowledge-manufacturing layer, not a runtime feature** — Claude builds the knowledge
offline, then the index forgets it ever existed.

The honest cost: **the LLM's mistakes freeze into the data.** A hallucinated alias or a dropped field
ships until something catches it — which is why eval sets and coverage gates aren't polish, they're the
safety net. The missing Korean and the empty `REGION_NAME` are exactly this, caught late because those
gates didn't exist yet.

## Data

- **Source:** OurAirports CSV — 85,477 rows.
- **Pruning:** kept `large`/`medium` airports with `scheduled_service = yes`, a valid IATA code, and a
  `home_link` (proxy for live commercial operation). **85,477 → 1,258** — drops heliports, military
  strips, dead entries. I'd rather under-cover than surface an airstrip.
- **Enrichment (per airport, via Claude):** normalized `CITY_NAME`/`COUNTRY_NAME`, `ALIASES`,
  `TRANSLATIONS` (11 languages), `POPULARITY_SCORE`, `_geo`.
- **Reliability:** checkpoint after every record, failures appended to JSONL for targeted re-runs,
  defensive JSON extraction (strips fences/prose/escaped literals).

## Search approach — Meilisearch, not Elasticsearch

Meilisearch ships typo tolerance, accent folding, prefix search, and CJK segmentation out of the box,
deploys as a single binary, and is fast enough to query per keystroke. Elasticsearch is more powerful,
but at 1,258 docs its tuning surface is mostly cost. The call is reversible — the matching layer is
decoupled from the enriched data.

| | Meilisearch (chosen) | Elasticsearch |
|---|---|---|
| Setup / ops | single binary | JVM, heap, cluster |
| Typo tolerance / accents | built in | configurable |
| CJK segmentation | built in (jieba / lindera) | plugins (kuromoji, nori, smartcn) |
| Relevance control | one ranking-rules list | deep, per-field |
| **Substring / n-grams** | **not supported** | **`ngram`/`edge_ngram`, synonym, phonetic** |

**The one real downside — n-grams.** Meilisearch does word-*prefix* matching only, on the last term
only; no n-grams, no infix matching. CJK is segmented into whole-word tokens, so for `東京` (Tokyo):
the full token matches, the prefix `東` matches, but the mid-token `京` does **not** — it's neither a
prefix nor its own token, and there's no character n-gram to fall back on. Elasticsearch's
`ngram`/`edge_ngram` plus per-language analyzers (`nori`, `kuromoji`) and synonym/phonetic filters would
make any substring matchable — at the cost of much larger, noisier indexes and heavier ops. At 1,258
enriched docs the enrichment already supplies the surface forms n-grams would brute-force, so it rarely
bites.

**Not the same as the Korean gap:** Meilisearch's lindera segmenter handles Korean fine — I just never
generated the Korean strings. Engine limit ≠ data gap.

## What works vs. what I got wrong

| Case | Status | Why |
|---|---|---|
| `JFK`, `TUL`/`Tulsa`, `São Paulo`, `Londn` | works | IATA + city indexed, accent folding, typo tolerance |
| `東京`, `北京`, `دبي` (CJK, Arabic) | works | `TRANSLATIONS` indexed |
| `Brussels`, `Bali`→DPS | works *if* in `ALIASES` | data-dependent |
| `LON` multi-airport city | works | all London airports return |
| `London` UK vs Ontario vs Kentucky | partial | all surface; only country shown to disambiguate |
| popularity ranking (big hub first) | works | `POPULARITY_SCORE:desc` is top rule |
| exact-beats-fuzzy | weak | `exactness` is last, below popularity |
| **`서울` / Korean** | **fails** | **Korean translations never generated (data gap)** |
| **`Hawaii` / `Ontario` state search** | **fails** | **`REGION_NAME` empty on all 1,258 records** |

### Known data gaps (owned)

1. **Korean missing** — the prompt covered 11 languages but not Korean; `서울`/`도쿄` return nothing.
   One-line prompt fix plus a re-run.
2. **`REGION_NAME` empty on all records** — the LLM silently dropped it, so state/region search fails.
   Should be a deterministic `iso_region`→name map, not an LLM field.
3. **3 / 1,258 records unenriched** — Bedrock failures logged to JSONL, never reprocessed.
4. **Index settings live in the instance, not the repo** — ranking rules set via API, so a fresh deploy
   comes up on defaults. Belongs in a seed step.

### The ranking call (deliberate, debatable)

Rules: `["POPULARITY_SCORE:desc", "words", "typo", "proximity", "attribute", "sort", "exactness"]`.
Putting popularity **first** makes it dominate — "Paris" surfaces CDG, which is what most users want —
but pushes `exactness` last, so a popular fuzzy match can outrank a perfect one. The alternative is
popularity as a late tie-breaker. I chose hub-first deliberately and would let an eval set decide
rather than assert it.

## LLM use & prompt iteration

- **Claude Sonnet 3.7 via Bedrock Agent** for enrichment — alias/translation generation is "world
  knowledge + structured output," which an LLM does and a static table doesn't.
- **Output discipline** was the first battle: responses came wrapped in prose and code fences. Rather
  than fight the prompt, I made the parser robust — `extract_json()` strips fences/prose/escaped
  literals and logs failures instead of crashing.
- **Where it was wrong:** it silently dropped `REGION_NAME` on every record. I caught it by
  coverage-counting the output (`0/1258`), not by spot-checking — the lesson: **validate structured
  LLM output by field-coverage, not eyeballing.** A coverage gate would have caught the missing Korean too.

## With another week

1. **Codify and re-tune ranking** — move rules into a seed step; A/B popularity-first vs
   popularity-as-tie-breaker on an eval set (I suspect exactness should win for typo'd queries).
2. **Fix the data gaps deterministically** — `iso_region`→name map, add Korean, reprocess the 3 failures.
3. **Collapse multi-airport cities** — LON → one "London (all airports)" row.
4. **Build the eval harness first** — I built the pipeline before the thing that proves it works.
   Backwards.
