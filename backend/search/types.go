package search

type Airport struct {
	IATA        string   `json:"iata_code"`
	Name        string   `json:"name"`
	CityName    string   `json:"CITY_NAME"`
	CountryName string   `json:"COUNTRY_NAME"`
	RegionName  string   `json:"REGION_NAME,omitempty"`
	Type        string   `json:"TYPE"`
	Popularity  int      `json:"POPULARITY_SCORE"`
	Geo         GeoPoint `json:"_geo"`
}

type GeoPoint struct {
	Lat float64 `json:"lat"`
	Lng float64 `json:"lng"`
}

type SearchResult struct {
	Hits             []Airport `json:"hits"`
	Query            string    `json:"query"`
	ProcessingTimeMs int64     `json:"processingTimeMs"`
	EstimatedTotal   int       `json:"estimatedTotalHits"`
}
