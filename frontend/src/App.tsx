import { useState, useRef, useEffect } from "react";
import { AirportCard } from "./components/airportCard";
import { useSearch } from "./api/useSearch";
import "./App.css";

const PLACEHOLDERS = [
  'Try "Tokyo"',
  'Try "Bali"',
  'Try "LHR"',
  'Try "São Paulo"',
  'Try "東京"',
  'Try "Hawaii"',
];

export default function App() {
  const [query, setQuery] = useState("");
  const [placeholderIdx, setPlaceholderIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const { result, status, error } = useSearch(query);

  useEffect(() => {
    const id = setInterval(() => {
      setPlaceholderIdx((i) => (i + 1) % PLACEHOLDERS.length);
    }, 2800);

    return () => clearInterval(id);
  }, []);

  const hasResults = !!result && result.hits.length > 0;
  const isEmpty = status === "success" && result?.hits.length === 0;

  return (
    <div className="page">
      <header className="header">
        <div className="wordmark">
          <span className="fly">Fly</span>
          <span className="fairly">Fairly</span>
        </div>

        <p className="tagline">Airport Search</p>
      </header>

      <div className="searchWrap">
        <div className={`inputRow ${query ? "active" : ""}`}>
          <svg className="icon" viewBox="0 0 20 20" fill="none">
            <circle
              cx="8.5"
              cy="8.5"
              r="5.5"
              stroke="currentColor"
              strokeWidth="1.5"
            />
            <path
              d="M13 13l3.5 3.5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>

          <input
            ref={inputRef}
            className="input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={PLACEHOLDERS[placeholderIdx]}
            autoComplete="off"
            spellCheck={false}
            autoFocus
          />

          {query && (
            <button
              type="button"
              className="clear"
              onClick={() => {
                setQuery("");
                inputRef.current?.focus();
              }}
            >
              ✕
            </button>
          )}
        </div>

        {status === "loading" && (
          <div className="status">
            <span className="spinner" />
          </div>
        )}

        {hasResults && result && (
          <div className="status">
            <span className="count">
              {result.estimatedTotalHits} result
              {result.estimatedTotalHits !== 1 ? "s" : ""}
            </span>

            <span className="timing">
              {result.processingTimeMs}ms
            </span>
          </div>
        )}
      </div>

      <main className="results">
        {error && <div className="message">{error}</div>}

        {isEmpty && (
          <div className="message">
            No airports found for "{query}"
          </div>
        )}

        {!query && (
          <div className="hint">
            Search by city, country, IATA code, region, or in any language
          </div>
        )}

        {hasResults &&
          result?.hits.map((airport, index) => (
            <AirportCard
              key={airport.iata_code}
              airport={airport}
              rank={index}
            />
          ))}
      </main>
    </div>
  );
}