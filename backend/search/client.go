package search

import (
	"github.com/meilisearch/meilisearch-go"
	"github.com/pnaskardev/airport-search-engine/backend/config"
)

type Client struct {
	index  meilisearch.IndexManager
	client meilisearch.ServiceManager
}

func NewClient(cfg config.Config) *Client {
	ms := meilisearch.New(cfg.MeilisearchURL,
		meilisearch.WithAPIKey(cfg.MeilisearchAPIKey),
	)
	return &Client{
		client: ms,
		index:  ms.Index(cfg.MeilisearchIndex),
	}
}
