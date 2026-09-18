"""Optional live API for scenario re-solve. Run locally for the demo —
every free container host cold-starts 30-60s (implementation.md §3.3).

    uvicorn urms.serve.api:app --port 8000
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

from urms.conf import load_config
from urms.decide.scenario import run_scenario

app = FastAPI(title="Aeon URMS")
cfg = load_config()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "city": cfg.city.slug}


@app.get("/scenarios")
def list_scenarios() -> list[dict]:
    path = Path(cfg.paths.build) / "scenarios.json"
    if not path.exists():
        raise HTTPException(404, "scenarios.json not built — run `make decide`")
    return json.loads(path.read_text())


@app.post("/scenarios/{scenario_id}/run")
def rerun(scenario_id: str) -> dict:
    scenario = next((s for s in cfg.scenarios if s["id"] == scenario_id), None)
    if scenario is None:
        raise HTTPException(404, f"unknown scenario '{scenario_id}'")
    return run_scenario(cfg, scenario)
