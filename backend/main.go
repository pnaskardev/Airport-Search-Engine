package main

import (
	"log"
	"net/http"

	"github.com/joho/godotenv"
	"github.com/pnaskardev/airport-search-engine/backend/config"
	"github.com/pnaskardev/airport-search-engine/backend/handler"
	"github.com/pnaskardev/airport-search-engine/backend/search"
)

func cors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")

		// Handle preflight requests
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusOK)
			return
		}

		next.ServeHTTP(w, r)
	})
}

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
	log.Fatal(http.ListenAndServe(cfg.Port, cors(mux)))
}
