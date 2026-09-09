import type { Airport } from "../types/airport";
import "../App.css";

interface Props {
  airport: Airport;
  rank: number;
}

export function AirportCard({ airport, rank }: Props) {
  const isLarge = airport.TYPE === "LARGE";

  // City, region and country together, because any two of them can be
  // ambiguous on their own: "London, England, United Kingdom" has to be
  // distinguishable from "London, Ontario, Canada" at a glance.
  const location = [airport.CITY_NAME, airport.REGION_NAME, airport.COUNTRY_NAME]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="card" style={{ animationDelay: `${rank * 40}ms` }}>
      <div className="iata">{airport.iata_code}</div>

      <div className="body">
        <div className="name">{airport.name}</div>

        <div className="location">{location}</div>
      </div>

      <div className="meta">
        <span className={`pill ${isLarge ? "large" : "medium"}`}>
          {isLarge ? "Major" : "Regional"}
        </span>
      </div>
    </div>
  );
}
