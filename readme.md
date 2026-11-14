# AirScanner

Airport search that handles how people actually type. IATA codes, city names, states and
provinces, tourist regions, typos, accents, and twelve languages in their own scripts —
answered in single-digit milliseconds from a local index, with no model in the request path.

3,276 commercial airports across 237 countries. Go API, Meilisearch, React, and an offline
enrichment pipeline that runs on Claude.

```
"Hawaii"  →  HNL · OGG · LIH · KOA        a US state, not an airport name
"Bali"    →  DPS                          a tourist region, and not Balikpapan
"Londn"   →  LHR · LGW · LCY · STN · LTN  one character short
"東京"     →  HND · NRT                    Japanese, in Japanese
"Roma"    →  FCO                          the endonym, not the English name
"TUL"     →  TUL                          a code, matched exactly, not fuzzily
```

---

## Why this is harder than it looks

Every flight booking starts in a search box, and a search box for airports has to absorb
almost every way a person can refer to a place:

- **Codes.** `JFK` is an airport. `LON` is a *city* code covering five of them. Neither should
  fuzzy-match its neighbours — `LON` and `LAX` are one edit apart and mean different things.
- **Administrative regions.** "Hawaii" and "Ontario" are not airport names, not city names, and
  not in most airport datasets at all.
- **Tourist regions.** People fly to Bali, not to Denpasar. They fly to Goa, not Dabolim.
- **Scripts.** A traveller in Seoul types `서울`. One in Cairo types `دبي` for Dubai. Neither
  string appears anywhere in an English airport database.
- **Endonyms and exonyms.** `Roma` / Rome, `München` / Munich, `São Paulo` with and without the
  accent — the same place, four spellings.
- **Near-collisions.** "Florida" the US state must beat "La Florida" in Chile. "Bali" must beat
  "Balikpapan". Fuzzy matching alone gets both of these wrong.
- **Dirty source data.** Public airport datasets are full of heliports, seaplane bases, military
  strips and airports with no commercial service.

A substring match over the airport name handles roughly half of these. Measured: **14 of 29**.

---

## Results

`eval.py` runs 29 queries covering every case above, against the live API. Each one asserts
both what must appear near the top *and* what must not — the near-collisions are half the test.
The same 29 run against a naive substring matcher, which is the baseline to beat.

| | Passing |
|---|---|
| Naive substring over airport name | 14 / 29 |
| **AirScanner** | **27 / 29** |

The two current failures are honest data gaps, not engine limits — see
[Known gaps](#known-gaps).

Median query latency is single-digit milliseconds. No LLM call happens during a search; all
of that work is paid once, offline.

---

## How it works

```
React (Vite)  ──►  Go API  ──►  Meilisearch
                  /api/search       ▲
                                    │ seed.py  (documents + index settings)
   OurAirports CSVs ──► backfill.py ──► airports_indexed.json
                            ▲
                            │ aliases + translations
                    enrich.py  (Claude Opus 5, Batch API, offline)
```

**The core idea: airport search is entity resolution, not string matching.** A user types
`JFK`, `New York`, `紐約`, `Nueva York`, or `JFKK` — many surface forms, one entity. Fuzzy
matching optimises string proximity when the real target is *intent*.

That gap can be closed at **query time** (expand every keystroke — powerful, but slow, costly
and hard to test) or at **index time** (make the document already contain every form a user
might type, so matching becomes a trivial lookup). AirScanner does it at index time.

Each airport is a deliberately fat record: IATA and ICAO codes, city, region, country, informal
aliases, the source dataset's curated keywords, and city plus airport names in twelve
languages. Once a document literally contains "Tokyo", "東京", "Haneda" and "HND", the runtime
needs no cleverness — and the expensive, non-deterministic part runs once per airport instead
of once per keystroke.

This is the standard place-search playbook: denormalised documents, alias expansion,
multilingual names, and a popularity prior so the busy hub wins ties.

| Component | Stack | Path |
|---|---|---|
| Search API | Go 1.26, `net/http` | [backend/](backend/) |
| Index | Meilisearch | container |
| Document build | Python 3, standard library | [data/pipeline/backfill.py](data/pipeline/backfill.py) |
| Index seed | Python 3, standard library | [data/pipeline/seed.py](data/pipeline/seed.py) |
| Eval harness | Python 3, standard library | [data/pipeline/eval.py](data/pipeline/eval.py) |
| Enrichment | Python 3, `anthropic` | [data/pipeline/enrich.py](data/pipeline/enrich.py) |
| Enrichment prompt | versioned Markdown | [data/pipeline/prompts/enrich_v2.md](data/pipeline/prompts/enrich_v2.md) |
| UI | React 19, Vite | [frontend/](frontend/) |

---

## Running it

The enriched data is committed, so you do not need an API key to run the search.

```bash
export MEILI_MASTER_KEY=masterKey          # or put it in .env at the repo root

docker compose up -d meilisearch           # index
docker compose up seed                     # one-shot: settings + documents, then exits
docker compose up -d backend               # API on :8080

python3 data/pipeline/eval.py              # expect 27/29 vs 14/29 baseline
```

Then query it:

```bash
curl "localhost:8080/api/search?q=Hawaii"
```

The frontend builds separately:

```bash
cd frontend && bun install && bun run dev
```

`backfill.py`, `seed.py` and `eval.py` are standard library only — no virtualenv, no install
step, and the seed service runs on a bare `python:3.13-slim` image.

### The pipeline, script by script

Run in this order. In practice you only ever run the last three.

**1. `enrich.py`** — asks Claude for what a CSV cannot know.

Sends one Batch API request per airport and writes `data/enriched/airport_enriched.json`. It
asks for exactly five things: city, country, aliases, translations in twelve languages, and a
popularity score. Everything else is derivable, and an LLM should not be paid to derive it.

Slow and costs money (~1 hour, ~$25 at Opus 5 batch rates). The output is committed, so this
step is normally skipped.

```bash
pip install -r data/pipeline/requirements.txt
export ANTHROPIC_API_KEY=...
python3 data/pipeline/enrich.py --estimate     # price it first, no spend
python3 data/pipeline/enrich.py --limit 5      # smoke test
python3 data/pipeline/enrich.py                # full run, resumable with --batch-id
```

**2. `backfill.py`** — builds the documents.

Joins the enrichment onto the raw rows by IATA code, applies the commercial-airport filter,
resolves the deterministic fields, and writes `airports_indexed.json` (3,276 documents). Fast,
offline, no dependencies.

**3. `seed.py`** — pushes to Meilisearch.

Index settings first, then documents. Settings before documents matters: the same documents
under default ranking rules are a different search engine.

**4. `eval.py`** — proves nothing broke.

29 cases against the running API, plus the naive baseline. Exits non-zero on any failure, so
CI can gate on it. `--baseline-only` needs no running stack.

### Why three CSVs

All three are OurAirports tables, normalised the way any database normalises — the airport row
stores a *code*, not a name.

| File | Rows | Holds | Join key |
|---|---|---|---|
| `airports_raw.csv` | 85,477 | the airports | — |
| `regions.csv` | 3,987 | `US-HI` = "Hawaii" | `iso_region` |
| `countries.csv` | 249 | `NR` = "Nauru" | `iso_country` |

An airport row says `iso_region: "US-HI"`. Nobody searches for "US-HI". `regions.csv` turns it
into "Hawaii", which is what people type. That join is the entire reason state and province
search works.

---

## Engineering notes

The interesting parts of this project were not the happy path. Three decisions took real
measurement to get right, and two of them started out wrong.

### Coverage: the filter that quietly deleted the world

Public airport data needs pruning — 85,477 rows are mostly heliports and dirt strips. The first
filter kept `large`/`medium` airports with scheduled service, a valid IATA code, **and a
non-empty `home_link`**, on the reasoning that a website is a proxy for active commercial
operation.

It is not. `home_link` measures whether a volunteer added a URL to OurAirports, which tracks
editor attention, not whether you can book a flight.

| | with `home_link` | without |
|---|---|---|
| Airports | 1,310 | 3,276 |
| Large airports dropped | 402 | 0 |

It removed Atlanta — the busiest airport on earth — along with Rome Fiumicino, Cairo, Kuala
Lumpur and Tulsa. And it removed them unevenly: China kept 25 of 223 airports, Colombia 3 of
42, Algeria 2 of 28. Cuba, Mozambique, Papua New Guinea and Uzbekistan kept none at all.

For a product whose whole point is serving travellers everywhere, that is not a coverage bug,
it is the wrong product. The current filter is `type` + `scheduled_service` + IATA code, and
nothing else.

**The takeaway is process, not data.** One `len()` before and after would have caught it on day
one. I was already coverage-checking the *model's* output and never thought to coverage-check
my own filter.

### Ranking: two rules that fight, settled by measurement

Meilisearch ranks by an ordered list of rules, and two of them pull in opposite directions:

- `exactness` — did the query match a whole word, or only a prefix? This is what keeps "Florida"
  the US state above "La Florida" in Chile, and "Bali" above "Balikpapan".
- `attribute` — *which field* matched? An earlier field in `searchableAttributes` scores higher.

Version one put `POPULARITY_SCORE` first. That buried `exactness` at the bottom and let a busy
fuzzy match beat a perfect one. Moving popularity to last fixed that — and broke something
subtler. With `attribute` now above popularity, Lakeland (popularity 410) outranked Tampa (800)
on the query `Florida`, because Lakeland happened to have "Florida" in its generated aliases
while Tampa only had it in `REGION_NAME`. TPA landed at rank 18, below two airports in South
America. Meilisearch's `showRankingScoreDetails` made it visible: `attributeRankingOrderScore`
0.75 versus 0.375.

Final order:

```
words · typo · proximity · exactness · POPULARITY_SCORE:desc · attribute · sort
```

Exactness above popularity, popularity above `attribute`, so uneven enrichment coverage can
never decide rank.

Typo tolerance starts at 5 characters rather than the default 4. At 4, `Bali` is one edit from
`Bari` and prefix-matches `Balikpapan`, and junk outranked Denpasar. `Londn` is 5 characters, so
realistic typo tolerance survives.

Both of these were found by running the eval, not by reasoning about them. That is the argument
for having one.

### Enrichment: what to ask a model, and what to just look up

The enrichment prompt is in the repo and versioned:
[`prompts/enrich_v2.md`](data/pipeline/prompts/enrich_v2.md), with a changelog naming the bug
behind each rule.

Version one ran on Bedrock Agents and failed in three silent ways:

1. **Prose instead of JSON.** Responses arrived wrapped in commentary and code fences, so the
   pipeline carried forty lines of defensive parsing that stripped fences, unwrapped string
   literals and hunted for the outermost braces. For three airports the agent returned a
   *clarifying question* instead of data, and those records got indexed anyway with no city and
   no country.
2. **A field it dropped.** It returned nothing for `REGION_NAME` on all 1,258 records, so every
   state and province search failed. Caught by coverage-counting the output (`0/1258`), not by
   spot-checking.
3. **A field never asked for.** The prompt named eleven languages in prose. Korean was not among
   them — and because translations came back as a flat list of unlabelled strings, there was no
   way to notice.

Version two runs on Claude Opus 5 through the first-party API, and each fix is structural
rather than defensive:

- **Structured outputs.** `output_config.format` pins a JSON schema with
  `additionalProperties: false` and every field `required`. Prose, code fences, clarifying
  questions and missing fields are all unrepresentable. The forty-line parser is deleted.
- **Translations are an object keyed by language**, all twelve `required`. A missing language is
  now a validation failure instead of an invisible hole.
- **`REGION_NAME` is gone from the prompt entirely.** It was never world knowledge — it is a
  `regions.csv` join. Same for country names. The general rule this encodes: *if a field is a
  join, join it; only ask a model for what no table has.*
- **Batch API.** 3,276 airports is offline bulk work with no latency requirement, so it runs at
  50% of standard pricing and replaces a one-request-per-30-seconds loop that needed 10.5 hours.
  The instruction block is byte-identical across every request, so it is the cached prefix and
  the airport row goes after the breakpoint.

The broader lesson from all three: **validate structured model output by field coverage, not by
reading a few samples** — and check whether the field is knowledge at all before spending a
model on it.

### Why Meilisearch and not Elasticsearch

Meilisearch ships typo tolerance, accent folding, prefix search and CJK segmentation out of the
box, runs as a single binary, and is fast enough to query on every keystroke. Elasticsearch is
more powerful, but at a few thousand documents its tuning surface is mostly cost. The decision
is also reversible — the matching layer is decoupled from the enriched data.

| | Meilisearch | Elasticsearch |
|---|---|---|
| Setup and ops | single binary | JVM, heap, cluster |
| Typo tolerance, accents | built in | configurable |
| CJK segmentation | built in (jieba / lindera) | plugins (kuromoji, nori, smartcn) |
| Relevance control | one ranking-rules list | deep, per-field |
| **Substring / n-grams** | **not supported** | **`ngram`, `edge_ngram`, synonym, phonetic** |

The real trade-off is n-grams. Meilisearch does word-*prefix* matching only, on the last term
only. CJK segments into whole-word tokens, so for `東京` the full token matches and the prefix
`東` matches, but the mid-token `京` does not. Elasticsearch's n-gram filters and per-language
analyzers would make any substring matchable, at the cost of much larger, noisier indexes and
much heavier ops. Index-time enrichment already supplies the surface forms n-grams would
brute-force, so it rarely bites.

---

## Known gaps

Tracked honestly rather than hidden — `eval.py` reports all of them.

| Gap | Detail |
|---|---|
| `Roma` → FCO fails | Fiumicino was one of the airports the old `home_link` filter dropped, so it was never enriched and has no Italian exonym. `Rome` works. |
| `서울` → ICN fails | Korean was absent from the v1 prompt, and Incheon's source `keywords` field is empty, so there was no fallback. Fixed in the v2 prompt; needs the batch re-run. |
| 2,021 of 3,276 airports unenriched | They carry raw name, city, region and IATA code — findable, but English-only. Both failures above are instances of this. One enrichment run closes all of it. |
| No multi-airport city collapse | `LON` returns five London airports as five rows, rather than one "London (all airports)" result. |
| `_geo` is indexed but unused | No "airports near me" yet. |
| No timeout on the Meilisearch call | If the index is unreachable, the handler hangs instead of failing fast. |

## Roadmap

1. Run the v2 enrichment across all 3,276 airports. Closes both eval failures and makes the
   twelve-language claim true for the whole index rather than 38% of it.
2. Collapse multi-airport cities into a single result row.
3. Geo search off the `_geo` field already in every document.
4. Rank on click-through per query instead of a static popularity prior. Whether CDG should beat
   ORY for "Paris" depends on the traveller, not on the string — the offline prior should only
   exist to cold-start the real signal.

## Licence

MIT — see [LICENSE](LICENSE).
