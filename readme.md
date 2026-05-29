docker run -it --rm \
 -p 7700:7700 \
 -e MEILI_ENV=development \
 -v $(pwd)/meili_data:/meili_data \
 getmeili/meilisearch:latest \
 meilisearch --master-key="this-is-a-very-secure-master-key-123"
