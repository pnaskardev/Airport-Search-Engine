# Airport enrichment prompt — v2

Used by `enrich.py`. v1 was a Bedrock Agent prompt that lived in the AWS console and is
not recoverable; what it asked for is reconstructed in the changelog below.

## System prompt

> You add search surface forms to airport records.
>
> A traveller searching for one airport types many different things: the IATA code, the city,
> the airport's informal name, the tourist region it serves, or the city in their own language
> and script. Your job is to list those forms so a plain text index can match them.
>
> Rules:
>
> - Return only forms a real traveller would type. "Bali Airport" for DPS: yes. "Airport in
>   Indonesia": no.
> - Include the tourist region when it is what people actually search. DPS serves Bali, so
>   "Bali" belongs in the aliases even though the city is Denpasar.
> - Include the friendly city name when the dataset's municipality is a suburb people do not
>   use. Brussels Airport is in Zaventem; "Brussels" belongs in the aliases.
> - For each language, give the city name and the airport name in that language's own script.
>   Use the form a native speaker would type, not a transliteration of English.
> - Where a language has a well-known endonym or exonym that differs from English, use it:
>   Roma for Rome in Italian, München for Munich in German.
> - If you are not confident about a name in some language, give the best-known local form
>   rather than inventing one. Do not leave it empty.
> - `popularity` ranks how likely this airport is to be the one a traveller means. A major
>   international hub is 900-1000, a large national airport 700-900, a busy regional airport
>   400-700, a small scheduled-service airport 200-400.

The user message is the airport's raw OurAirports row as JSON.

## Response format

Enforced by `output_config.format` (JSON schema, `additionalProperties: false`, every field
in `required`) — see `SCHEMA` in `enrich.py`. The model cannot return prose, a code fence, a
clarifying question, or a partial object.

## Changelog

### v2 — Claude Opus 5 via the Anthropic API, Batch API

Moved off Bedrock Agents to the first-party API. Four changes, each fixing an observed v1 bug:

1. **Structured outputs replace prose parsing.** v1 responses arrived wrapped in prose and
   code fences, so v1 carried a 40-line `extract_json()` that stripped fences, unwrapped
   Python string literals and hunted for the outermost braces. For three airports (SWO, TKK,
   HID) the agent returned a *clarifying question* instead of JSON, and those records were
   indexed anyway with no city and no country. A JSON schema makes all of that unrepresentable,
   and the parser is deleted.

2. **`REGION_NAME` removed from the prompt.** v1 asked for it and the model silently returned
   nothing for it on all 1,258 records, so every state and province search failed. It was never
   world knowledge — `iso_region` maps to a region name through OurAirports' own `regions.csv`.
   `backfill.py` does it deterministically now.

3. **Korean added, and every language is `required`.** v1 asked for 11 languages in prose and
   Korean was not among them, so `서울` matched nothing and nobody noticed. v2 lists 12
   languages as required schema properties, so a missing language is a validation failure
   rather than a silent gap.

4. **Translations are an object keyed by language, not a flat list.** v1 returned a flat array
   of 22 strings with no indication of which language each belonged to, which is why the
   missing Korean was invisible. Per-language coverage is now checkable by reading the keys.

Also: the Batch API replaces the one-request-per-30-seconds loop (10.5 hours of `time.sleep`
for 1,258 airports) at half the token price, and the shared instruction block is cached.
