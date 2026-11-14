package search

type SearchResult struct {
	Hits             []Airport `json:"hits"`
	Query            string    `json:"query"`
	ProcessingTimeMs int64     `json:"processingTimeMs"`
	EstimatedTotal   int       `json:"estimatedTotalHits"`
}
