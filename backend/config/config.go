package config

import "os"

type Config struct {
	MeilisearchURL    string
	MeilisearchAPIKey string
	MeilisearchIndex  string
	Port              string
}

func Load() Config {
	return Config{
		MeilisearchURL:    getEnv("MEILISEARCH_URL", "http://localhost:7700"),
		MeilisearchAPIKey: getEnv("MEILI_MASTER_KEY", "masterKey"),
		MeilisearchIndex:  getEnv("MEILISEARCH_INDEX", "airports"),
		Port:              getEnv("PORT", ":8080"),
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
