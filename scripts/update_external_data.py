from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.server import build_epi_county_budget, build_phd_comparison  # noqa: E402


DATA_DIR = ROOT / "extra_data"


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main() -> None:
    updated_at = datetime.now(timezone.utc).isoformat()
    DATA_DIR.mkdir(exist_ok=True)

    write_json(
        DATA_DIR / "phdstipends-comparison.static.json",
        {
            "updated_at": updated_at,
            "rows": build_phd_comparison(),
        },
    )

    epi_payload = build_epi_county_budget()
    epi_payload["updated_at"] = updated_at
    write_json(DATA_DIR / "epi-family-budget.static.json", epi_payload)


if __name__ == "__main__":
    main()
