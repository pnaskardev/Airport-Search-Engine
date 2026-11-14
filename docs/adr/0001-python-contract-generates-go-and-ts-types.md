# Python Contract generates the Go and TypeScript Airport types

The Contract (the Served fields of an Airport document) is defined once in `data/pipeline/airport_contract.py`. `gen_types.py` generates the Go `Airport` struct, the `AttributesToRetrieve` list and the TypeScript `Airport` interface from it, and `backfill.py` validates every document against it. The generated files are committed and never edited by hand.

Python owns the Contract because the pipeline is where Airport documents are built; Go and TypeScript only consume them over HTTP. A language-neutral JSON Schema and a Go-owned struct were considered. Both would leave one language's hand-written types to be kept in sync by convention, which is how `TYPE` and `_geo` drifted between Go and TypeScript.

Consequence: a change to a Served field starts in the pipeline, and the generator must be re-run before the Go and TypeScript sides see it. There is no `--check` mode or CI step yet, so forgetting to regenerate is not caught automatically.
