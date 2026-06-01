import type { Airport } from "../types/airport";
import "../App.css";

interface Props {
  airport: Airport;
  rank: number;
}

export function AirportCard({ airport, rank }: Props) {
  const isLarge = airport.TYPE === "LARGE";

  const location = airport.REGION_NAME
    ? `${airport.REGION_NAME}, ${airport.COUNTRY_NAME}`
    : `${airport.CITY_NAME}, ${airport.COUNTRY_NAME}`;

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
