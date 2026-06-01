export interface Airport {
  iata_code: string;
  name: string;
  CITY_NAME: string;
  COUNTRY_NAME: string;
  REGION_NAME?: string;
  TYPE: "LARGE" | "MEDIUM";
  POPULARITY_SCORE: number;
  _geo?: { lat: number; lng: number };
}

export interface SearchResult {
  hits: Airport[];
  query: string;
  processingTimeMs: number;
  estimatedTotalHits: number;
}
