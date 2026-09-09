package search

import (
	"encoding/json"
	"strings"

	"github.com/meilisearch/meilisearch-go"
)

const (
	defaultLimit = 10
	maxLimit     = 20
)

func (c *Client) Search(q string) (*SearchResult, error) {
	q = strings.TrimSpace(q)
	if q == "" {
		return &SearchResult{}, nil
	}

	params := &meilisearch.SearchRequest{
		Limit: int64(defaultLimit),
		AttributesToRetrieve: []string{
			"iata_code", "name", "CITY_NAME", "COUNTRY_NAME",
			"REGION_NAME", "TYPE", "POPULARITY_SCORE", "_geo",
		},
	}

	raw, err := c.index.Search(q, params)
	if err != nil {
		return nil, err
	}

	return marshalResult(q, raw)
}

// marshalResult converts the raw Meilisearch response into our typed struct.
// Keeps JSON unmarshalling in one place rather than spread across the
// handler and the search logic.
func marshalResult(q string, raw *meilisearch.SearchResponse) (*SearchResult, error) {
	// marshal hits back to JSON then into []Airport
	// (Meilisearch Go SDK returns []any for hits)
	hits := make([]Airport, 0, len(raw.Hits))
	for _, h := range raw.Hits {
		b, err := json.Marshal(h)
		if err != nil {
			continue // skip malformed hit, don't fail the whole search
		}
		var airport Airport
		if err := json.Unmarshal(b, &airport); err != nil {
			continue
		}
		hits = append(hits, airport)
	}

	return &SearchResult{
		Hits:             hits,
		Query:            q,
		ProcessingTimeMs: raw.ProcessingTimeMs,
		EstimatedTotal:   int(raw.EstimatedTotalHits),
	}, nil
}
