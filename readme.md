# Fly Fairly — Airport Search Engine

Typo-tolerant, multi-script airport search. A Go API in front of a Meilisearch index,
seeded with OurAirports data enriched offline (aliases + translations + popularity) via
AWS Bedrock / Claude. React UI on top.

## Architecture

```
React (Vite)  ──►  Go API  ──►  Meilisearch
                  /api/search       ▲
                                    │ seed.py (documents + index settings)
   OurAirports CSVs ──► backfill.py ──► airports_indexed.json
                            ▲
                            │ aliases + translations
                    enrich.py (Bedrock / Claude, offline)
```

| Component | Stack | Path |
|---|---|---|
| Search API | Go 1.26, net/http | [backend/](backend/) |
| Index | Meilisearch | container |
| Document build | Python 3, standard library | [data/pipeline/backfill.py](data/pipeline/backfill.py) |
| Index seed | Python 3, standard library | [data/pipeline/seed.py](data/pipeline/seed.py) |
| Eval harness | Python 3, standard library | [data/pipeline/eval.py](data/pipeline/eval.py) |
| LLM enrichment | Python 3, boto3, pandas | [data/pipeline/enrich.py](data/pipeline/enrich.py) |
| UI | React 19 + Vite | [frontend/](frontend/) |

## Run it

```bash
export MEILI_MASTER_KEY=masterKey
docker compose up -d meilisearch backend   # seed runs automatically, then exits
python3 data/pipeline/eval.py              # 27/29 vs 14/29 for naive substring
```

`backfill.py` and `seed.py` need no dependencies. `enrich.py` is the offline Bedrock pass
and only needs to run when regenerating aliases and translations.

---

# Approach Memo

**Stack:** Go API + Meilisearch + React/Vite, Python pipeline (standard library for the
document build and seed, boto3 + Bedrock for the offline enrichment), wired with Docker
Compose.

**The core bet: do the hard linguistic work offline, keep the runtime a dumb fast lookup.**
Aliases and multi-script names are pre-computed per airport once and folded into searchable
fields, so a typo-tolerant index does all the matching. The expensive, non-deterministic LLM
is paid for once per airport, never per search — latency stays in single-digit milliseconds
with no runtime model dependency.

## The idea we built on

**Airport search is entity resolution, not string matching.** A user types `JFK`, `New York`,
`紐約`, `Nueva York`, or `JFKK` — many surface forms, one entity. Naive single-field fuzzy
matching fails the brief's cases (`Florida` pulling in `La Florida`, `Bali` pulling in
`Balikpapan`) because it optimizes string proximity when the real target is *intent*.

You can close that gap at **query time** (expand every keystroke — powerful but slow, costly,
non-deterministic, hard to test) or at **index time** (enrich the document so it already
contains every form a user might type, making matching a trivial lookup). **I chose index-time
enrichment:** each airport is a "fat" record carrying its IATA code, city, region, country,
aliases, curated keywords and names across 11 languages. Once the document says "Tokyo,"
"東京," "Haneda," and "HND," the runtime needs no cleverness.

This is the standard place-search playbook — denormalized documents, alias expansion,
multilingual names, and a popularity prior so the busy hub wins ties. A mature version is a
curated place graph; I approximated it with LLM-generated enrichment instead of licensing a
dataset.

## Data

- **Source:** OurAirports `airports.csv` (85,477 rows), plus `regions.csv` and `countries.csv`.
- **Pruning:** `large`/`medium` airports with `scheduled_service = yes` and a valid IATA code.
  **85,477 → 3,276.** That drops heliports, seaplane bases, military strips and everything
  with no commercial service.
- **Enrichment (via Claude on Bedrock):** normalized `CITY_NAME`/`COUNTRY_NAME`, `ALIASES`,
  `TRANSLATIONS` (11 languages), `POPULARITY_SCORE`. Covers 1,255 of 3,276 airports.
- **Deterministic fields, no LLM:** `REGION_NAME` from `iso_region` via `regions.csv`,
  `COUNTRY_NAME` fallback via `countries.csv`, `KEYWORDS` straight from the OurAirports
  `keywords` column.
- **Reliability:** checkpoint after every record, failures appended to JSONL for targeted
  re-runs, defensive JSON extraction (strips fences, prose and escaped literals).

### The pruning mistake, and what it cost

The first version of the filter also required a non-empty `home_link`, justified as "a proxy
for live commercial operation." It is not one. It measures whether a volunteer added a URL to
OurAirports, and that correlates with editor attention, not with whether you can book a flight.

| | with `home_link` | without |
|---|---|---|
| Airports | 1,310 | 3,276 |
| Large airports dropped | 402 | 0 |

It removed ATL — the busiest airport in the world — plus FCO, TUL, CAI and KUL. `TUL` is one
of the brief's own named failure cases, and the filter guaranteed it would keep failing. Worse,
it removed them unevenly: China kept 25 of 223 airports, Colombia 3 of 42, Algeria 2 of 28;
Cuba, Mozambique, Papua New Guinea and Uzbekistan kept none at all. For a global travel product
that is not a coverage bug, it is the wrong product.

**The lesson is process, not data.** I never counted what the filter removed. One `len()` before
and after would have shown it immediately — the same coverage check I correctly applied to the
LLM's output and never applied to my own code.

Airports the LLM never scored keep their raw name, city, region and IATA code, so they are
findable; they just have no aliases or translations until the enrichment pass is re-run.

## Search approach — Meilisearch, not Elasticsearch

Meilisearch ships typo tolerance, accent folding, prefix search and CJK segmentation out of the
box, deploys as a single binary, and is fast enough to query per keystroke. Elasticsearch is more
powerful, but at a few thousand documents its tuning surface is mostly cost. The call is
reversible — the matching layer is decoupled from the enriched data.

| | Meilisearch (chosen) | Elasticsearch |
|---|---|---|
| Setup / ops | single binary | JVM, heap, cluster |
| Typo tolerance / accents | built in | configurable |
| CJK segmentation | built in (jieba / lindera) | plugins (kuromoji, nori, smartcn) |
| Relevance control | one ranking-rules list | deep, per-field |
| **Substring / n-grams** | **not supported** | **`ngram`/`edge_ngram`, synonym, phonetic** |

**The one real downside — n-grams.** Meilisearch does word-*prefix* matching only, on the last
term only. CJK is segmented into whole-word tokens, so for `東京` the full token matches and the
prefix `東` matches, but the mid-token `京` does not. Elasticsearch's `ngram`/`edge_ngram` plus
per-language analyzers would make any substring matchable, at the cost of much larger, noisier
indexes and heavier ops. The enrichment already supplies the surface forms n-grams would
brute-force, so it rarely bites.

## Ranking, decided by the eval rather than by taste

Two rules fight over every query. `exactness` asks whether the query matched a whole word or only
a prefix. `attribute` asks which field matched, scoring earlier fields higher.

The first version put `POPULARITY_SCORE` first, which buried `exactness` at the bottom and let a
busy fuzzy match beat a perfect one. Moving popularity to last fixed that and broke something
else: `attribute` then outranked popularity, and because enrichment coverage is uneven, Lakeland
(410) beat Tampa (800) on the query `Florida` — Lakeland happened to have "Florida" in its
LLM-written aliases while Tampa only had it in `REGION_NAME`. TPA landed at rank 18, below two
airports in Chile and Colombia.

Final order — `words, typo, proximity, exactness, POPULARITY_SCORE:desc, attribute, sort`.
Exactness above popularity keeps the US state ahead of "La Florida"; popularity above `attribute`
stops uneven enrichment from deciding rank.

Typo tolerance starts at 5 characters instead of the default 4. At 4, `Bali` is one edit from
`Bari` and prefix-matches `Balikpapan`, and junk outranked Denpasar. `Londn` is 5 characters, so
the brief's typo case still works.

Both changes were found by running the eval, not by reasoning about them.

## Evaluation

`eval.py` runs 29 cases drawn from the brief. Each asserts what must appear near the top *and*
what must not — the fuzzy traps are half the test. The same cases run against a naive substring
matcher over the airport name, which is the baseline the engine has to beat.

| | passed |
|---|---|
| Naive substring over `name` | 14 / 29 |
| This search | **27 / 29** |

The two remaining failures are honest data gaps, both needing an enrichment re-run:

| Case | Why |
|---|---|
| `Roma` → FCO | FCO was one of the airports the old filter dropped, so it was never enriched and has no Italian exonym. `Rome` works. |
| `서울` → ICN | The enrichment prompt covered 11 languages and Korean was not one of them. ICN's OurAirports `keywords` column is empty, so there was nothing to fall back on. |

Neither is an engine limitation. Meilisearch's lindera segmenter handles Korean; the strings were
never generated.

## LLM use, and where it was wrong

- **Claude Sonnet 3.7 via Bedrock Agent** for enrichment — alias and translation generation is
  "world knowledge plus structured output," which an LLM does and a static table does not.
- **Output discipline** was the first battle: responses came wrapped in prose and code fences.
  Rather than fight the prompt, I made the parser robust — `extract_json()` strips fences, prose
  and escaped literals, and logs failures instead of crashing.
- **Where it was wrong, quietly.** It returned nothing for `REGION_NAME` on all 1,258 records, so
  every state and province search failed. I caught it by coverage-counting the output (`0/1258`),
  not by spot-checking. It also asked a clarifying question instead of returning JSON for three
  airports, and those records were indexed anyway with no city and no country.
- **What I changed as a result.** `REGION_NAME` is a lookup from `iso_region`, not world
  knowledge, so it should never have been an LLM field at all — it is deterministic now. The
  three question-shaped records are dropped at build time.
- **What I should have asked first.** OurAirports already ships a hand-curated `keywords` column
  carrying native scripts (`NRT` has `東京`, `PEK` has `北京`) and multi-airport city codes (`TYO`,
  `BJS`, `LON`). The original pipeline discarded it and paid an LLM to reinvent a worse version.
  Indexing it is what made `東京` return both Tokyo airports.

The general lesson: **validate structured LLM output by field coverage, not by eyeballing**, and
check whether the field is knowledge at all before spending a model on it.

## Build vs buy vs fake

- **Real:** the document build, the index settings, the ranking order, the eval harness, the API.
- **Bought:** Meilisearch for matching, OurAirports for the base data, Bedrock for enrichment.
- **Not built:** multi-airport city collapse (`LON` returns all five London airports as separate
  rows rather than one "London (all airports)" result), and any use of `_geo`, which is indexed
  but unused.

## What I would do differently, and what I would push back on

1. **Build the eval first.** It found both ranking bugs and the coverage disaster in minutes, and
   it would have found them before submission rather than after. I built the pipeline before the
   thing that proves it works, which is backwards.
2. **Question every filter by measuring it.** The `home_link` mistake was one line of code and one
   missing `len()`.
3. **Re-run enrichment over all 3,276 airports** with Korean added and `REGION_NAME` removed from
   the prompt. That closes both remaining eval failures.
4. **Collapse multi-airport cities** so `LON` returns one row.
5. **Push back on:** "popularity ranking" is not a single correct answer, and the brief treats it
   as one. Whether CDG should beat ORY for `Paris` depends on the user's trip, not on the string.
   With real traffic I would rank on click-through per query, and the offline prior would exist
   only to cold-start it. I would want session data before defending any fixed order.
