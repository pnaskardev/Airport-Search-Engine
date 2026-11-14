import type { Airport } from "./airport.gen";

export type { Airport };

export interface SearchResult {
  hits: Airport[];
  query: string;
  processingTimeMs: number;
  estimatedTotalHits: number;
}
