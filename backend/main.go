package main

import (
	"log"
	"net/http"

	"github.com/joho/godotenv"
	"github.com/pnaskardev/airport-search-engine/backend/config"
	"github.com/pnaskardev/airport-search-engine/backend/handler"
	"github.com/pnaskardev/airport-search-engine/backend/search"
)

func main() {

	err := godotenv.Load()
	if err != nil {
		log.Println("No .env file found")
	}

	cfg := config.Load()
	searchClient := search.NewClient(cfg)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/search", handler.Search(searchClient))
	mux.HandleFunc("GET /api/health", handler.Health())

	log.Printf("listening on %s", cfg.Port)
	log.Fatal(http.ListenAndServe(cfg.Port, mux))
}
