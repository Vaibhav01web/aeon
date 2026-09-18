# Pravah — Urban Resource Flow Planner

*Plan the city's flows — water, power and waste, ward by ward.*

Team Musketeers · PCCOE Indradhanu · Track: Smart Cities, Energy & Circular Economy

**Live demo:** https://vaibhav01web.github.io/aeon/

Given a city name and bounding box, Pravah predicts per-zone demand for water,
electricity and solid waste with uncertainty bands, compares it against
infrastructure capacity inferred from OpenStreetMap, ranks shortage risk,
optimises allocation and waste-collection routing, detects anomalies, and
simulates scenarios (population growth, heatwaves, festivals, outages) — all
on open data, at zero budget. See `implementation.md` (project root, one
level up) for the full build spec this repository implements.

## Quickstart

```bash
make setup                 # installs deps, runs the endpoint doctor
make all CITY=pune          # acquire -> zones -> calibrate -> forecast -> decide -> export -> validate
make demo                   # serves web/ on http://localhost:8080
```

Switching city = editing one file: `config/cities/<slug>.yaml`. No city
name, bbox, CRS or coefficient may appear anywhere under `src/`.

## Deployment layer — iNSIGHTS

Pravah's forecast/allocation/scenario pipeline (this repo) is the underlying
prediction and optimisation engine. **iNSIGHTS** (insights-ai.info) is the
agentic AI OS a municipality would run it on: it polls `build/scenarios.json`
and `build/anomalies.json`, auto-triggers the relevant scenario re-solve when
an incoming signal (weather, anomaly z-score) warrants it, and pushes the
resulting allocation recommendation out through its existing Slack/Jira/GitHub
integrations — turning Pravah's demand forecast into automated, predictive
resource allocation *before* a shortage occurs, without a human having to
watch a dashboard. See the deck's iNSIGHTS slide for the exact framing.

## Licensing

Code: MIT (`LICENSE`). Data obligations: see `ATTRIBUTION.md` — building
footprints and OSM-derived data are ODbL and share-alike applies to any
derived database you publish.
