docker run -it --rm \
 -p 7700:7700 \
 -e MEILI_ENV=development \
 -v $(pwd)/meili_data:/meili_data \
 getmeili/meilisearch:latest \
 meilisearch --master-key="this-is-a-very-secure-master-key-123"

## run data enrichment pipeline

```
   cd data/pipeline
   python enrich.py --input ../raw/airports_raw.csv --output ../enriched/airport_enriched.json
```

65.2.170.26
