"""Export the peer airports (code, lat, lon, enplanements) used by the UI's home-screen map.

    uv run python scripts/export_map_points.py   ->  frontend/src/assets/airport-points.json

The map is drawn from the same snapshot the agent analyses, so it is real data, not decoration.
Re-run after `make ingest` to refresh it.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings
from app.data.repository import Repository
from app.services.insights import InsightService

OUT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "assets" / "airport-points.json"


def main() -> None:
    s = get_settings()
    svc = InsightService.from_paths(Repository(s.snapshot_dir), s.scoring_config_path, s.airport_notes_path)
    peers = svc.default_scores.table.index
    f = svc.features.loc[peers, ["latitude", "longitude", "enplanements_latest"]].dropna()
    points = [
        {
            "code": code,
            "lat": round(float(r.latitude), 3),
            "lon": round(float(r.longitude), 3),
            "pax": int(r.enplanements_latest),
        }
        for code, r in f.sort_values("enplanements_latest").iterrows()
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(points, separators=(",", ":")))
    print(f"wrote {len(points)} airports to {OUT}")


if __name__ == "__main__":
    main()
