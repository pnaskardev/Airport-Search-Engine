# Fly Fairly — Airport Search: Approach Memo

**Stack:** Go (HTTP API) + Meilisearch (index) + React/Vite (UI), Python for the offline data
pipeline, all wired together with Docker Compose. Enrichment runs on AWS Bedrock (Claude).

The core bet: **do the hard linguistic work offline, keep the runtime dumb and fast.** Instead of
reaching for an LLM or embeddings at query time, I pre-compute aliases and multi-script translations
per airport once, fold them into searchable fields, and let a typo-tolerant index do the matching.
Search latency stays in single-digit milliseconds and the runtime has no model dependency.

## Data — where it came from and what I did to it

- **Source:** OurAirports raw CSV — 85,477 rows.
- **Pruning (the most important step):** kept only `large_airport`/`medium_airport`, with
  `scheduled_service = yes`, a non-empty IATA code, *and* a `home_link` (a cheap proxy for "actually
  operates commercially"). That drops heliports, seaplane bases, military strips, and dead fields
  in one pass: **85,477 → 1,258 airports**. This directly answers the brief's "most airport
  databases are junk" — I'd rather under-cover than surface a military airstrip.
- **Enrichment (per airport, via Bedrock/Claude):** normalized `CITY_NAME` / `COUNTRY_NAME`, an
  `ALIASES` array (friendly/municipality/IATA-suffixed name variants), a `TRANSLATIONS` array across
  11 languages/scripts, a `POPULARITY_SCORE`, and `_geo` for geosearch.
- **Reliability:** checkpoint-after-every-record (no losing 800 rows when row 801 throttles),
  failures appended to a JSONL so only failures get re-run, defensive JSON extraction from the
  LLM response (strips code fences, prose preamble, Python-escaped literals).

## Search approach — what I chose and why

**Meilisearch, not Elasticsearch and not an LLM at runtime.** It ships typo tolerance, Unicode
normalization (accent folding), and prefix search out of the box, deploys as a single container, and
returns results fast enough to query on every keystroke. Elasticsearch buys more tuning power at a
much heavier operational cost I didn't need for 1,258 docs. A runtime LLM would be slower, costlier,
and non-deterministic for what is fundamentally a lookup.

Because aliases and translations are real fields on each document, the multi-script and city-name
cases work through *one* index rather than special-casing per language. "東京" and "Haneda Airport"
both match because both strings literally live on the Tokyo document.

## What works vs. what I cut or got wrong (honest)

| Case | Status | Why |
|---|---|---|
| `JFK`, `Tulsa`/`TUL` reverse asymmetry | works | IATA + city both indexed |
| `Sao Paulo` / `São Paulo`, `München` | works | accent folding + translations |
| `東京`, `北京`, `دبي` (CJK, Arabic) | works | `TRANSLATIONS` field indexed |
| `Brussels` not just `Zaventem`, `Bali`→DPS | works *if* Claude put it in `ALIASES` | data-dependent, not guaranteed |
| `Londn` typo | works | Meilisearch typo tolerance |
| **`서울` / Korean** | **fails** | **I never generated Korean translations — gap in the enrichment prompt, see below** |
| **`Hawaii` / `Ontario` state search** | **fails** | **`REGION_NAME` is empty on all 1,258 records** |
| `LON` → multi-airport city result | partial | all London airports return, but not collapsed into one city row |
| `London` UK vs Ontario vs Kentucky | partial | both surface; no explicit disambiguation UI |
| popularity ranking (big hub first) | works | `POPULARITY_SCORE:desc` is the top ranking rule |
| exact-beats-fuzzy | weak | `exactness` is dead-last, *below* popularity — see below |

### Data inconsistencies I hit (and own)

1. **Korean is missing.** The enrichment prompt produced English, Chinese, Hindi, Spanish, Arabic,
   French, Bengali, Portuguese, Russian, Indonesian, and Japanese — but I never added Korean to the
   target-language list. So "서울"→Seoul and "도쿄"→Tokyo return nothing. It's a one-line prompt fix
   plus a re-run, not an architecture problem, but it's live in the current dataset.
2. **`REGION_NAME` is empty on every record (0/1258).** The backend still asks Meilisearch to return
   it, and state/region searches ("Hawaii", "Ontario") depend on it — so that entire class of query
   currently fails. The raw `iso_region` (e.g. `US-HI`) is present; I should have mapped region codes
   to human names deterministically instead of relying on the LLM, which silently dropped the field.
3. **3 of 1,258 records have no enrichment at all** — the Bedrock failures that landed in the JSONL
   and were never reprocessed.
4. **Index settings live in the Meilisearch instance, not the repo.** I configured the ranking rules
   via the Meilisearch API, not in code, so a fresh `docker-compose up` comes up with defaults — the
   tuning isn't reproducible or version-controlled. It belongs in a seed step.

### The ranking call (deliberate, and debatable)

The ranking rules I set are:

```
["POPULARITY_SCORE:desc", "words", "typo", "proximity", "attribute", "sort", "exactness"]
```

Putting `POPULARITY_SCORE:desc` **first** makes popularity the dominant signal: Meilisearch
bucket-sorts on the first rule, and because popularity scores are granular almost every document is
its own bucket, so the built-in relevancy rules (`words`/`typo`/`exactness`) only break ties within
equal-popularity docs. The upside is exactly what most users want — "Paris" surfaces CDG, the big
hub, first. The downside, which I'd flag in review: `exactness` is now last, so a *more popular fuzzy
match can outrank a perfect one*. For unique IATA codes this is harmless, but for ambiguous or
typo'd queries it's a real risk. The defensible alternative is popularity as a late tie-breaker
(`words, typo, proximity, attribute, exactness, POPULARITY_SCORE:desc`) — relevance wins, popularity
only decides between equally-good matches. I chose hub-first deliberately; I'd want the eval set
(below) to tell me whether that's right rather than asserting it.

## LLMs and tools used

- **Claude (via AWS Bedrock Agent)** for offline enrichment — chosen because alias/translation
  generation is exactly "world knowledge + structured output," which an LLM does well and a static
  table does not.
- **Claude Code / an LLM coding assistant** for the Go service, the React UI, and the pipeline
  scaffolding.
- **Meilisearch** as the index (buy, not build).

### Prompt iteration & where the LLM was wrong

- **Output discipline was the first battle.** Early responses came wrapped in prose ("Sure, here's
  the JSON…") and markdown fences. Rather than fight the model into perfect output, I made the
  *parser* robust — `extract_json()` unwraps fences, prose, and escaped literals and isolates the
  outermost `{...}`. Failures get logged, not crashed on.
- **Fresh session per airport** — a shared session let earlier airports' context bleed into later
  ones (translations drifting toward the wrong city). A new `sessionId` per record fixed it.
- **Where it was wrong and I caught it:** it silently omitted `REGION_NAME` on essentially every
  record. I caught it by coverage-counting the output (`0/1258`), not by eyeballing samples — which
  is the lesson: **validate structured-LLM output by field-coverage metrics, not spot checks.**
- **What I'd have caught with a coverage gate:** the missing Korean too. A simple assert ("every
  record has ≥12 translations covering this script set") would have failed the build.

## Build vs buy vs fake

- **Built:** the data pipeline (filtering, checkpointing, failure handling), the Go search API, the
  React UI.
- **Bought:** Meilisearch (index, typo tolerance, accent folding), Bedrock (enrichment compute).
- **Faked / cut for time:** no eval harness, no automated tests, region-name mapping, and a codified
  seed step (the ranking rules are set live via API, not in the repo). Ranking *is* configured —
  popularity-first — but it's a single judgment call I haven't validated against a labelled set.

## How I'd evaluate this in production

- **A labelled query→expected-IATA eval set** built straight from the brief's failure cases plus a
  long tail, run on every index rebuild. Metrics: **recall@5** (did the right airport appear) and
  **MRR** (was it near the top). This is the single highest-value thing I cut.
- **A naive-substring baseline** to prove the index actually beats `LIKE '%q%'` — and to catch the
  "La Florida in Chile beats Florida the state" class of regression.
- **Coverage gates on the data** (every record has city/country/IATA, ≥N translations, a populated
  region) so a bad enrichment run fails loudly instead of silently shipping the Korean/region gaps.
- **Production signals:** zero-result rate by query, click-through position, and per-script query
  volume to see which languages actually matter to real users.

## With another week / what I'd push back on

1. **Codify and re-tune the ranking** — move the rules into a seed step so they're reproducible, set
   searchable-attribute ordering (IATA > city > name > aliases > translations), and A/B the
   popularity-first order against popularity-as-tie-breaker on the eval set. I suspect exactness
   should rank above popularity for typo'd/ambiguous queries.
2. **Fix the two data gaps deterministically** — region-code→name mapping in code (not via the LLM),
   add Korean to the translation set, reprocess the 3 failures.
3. **Collapse multi-airport cities** (LON → one "London (all airports)" row that fans out).
4. **Build the eval harness first** next time — I built the pipeline before the thing that proves the
   pipeline is good, which is backwards.
5. **Push back on you:** "global customer base, all scripts" is open-ended. I'd want real query-log
   data on which languages users actually search in before paying enrichment cost for all of them —
   right now I'm guessing at 11 languages and still got the set wrong (no Korean). Coverage should be
   demand-driven, not aspirational.
