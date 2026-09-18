CITY ?= pune
PY   := python -m urms.cli
export URMS_CITY := config/cities/$(CITY).yaml

.PHONY: all setup acquire zones calibrate forecast decide export validate demo clean

all: acquire zones calibrate forecast decide export validate

setup:
	pip install -e .
	sudo apt-get install -y osmium-tool
	$(PY) doctor                      # verifies every endpoint + extension

acquire:
	$(PY) acquire buildings
	$(PY) acquire sentinel --window current
	$(PY) acquire sentinel --window baseline
	$(PY) acquire ghsl
	$(PY) acquire osm
	$(PY) acquire weather
	$(PY) acquire stats

zones:      ; $(PY) zones build && $(PY) zones features
calibrate:  ; $(PY) calibrate floorspace && $(PY) calibrate population && $(PY) calibrate demand
forecast:   ; $(PY) forecast temporal && $(PY) forecast spatial && $(PY) forecast reconcile
decide:     ; $(PY) capacity infra && $(PY) capacity gap && $(PY) decide allocate \
	          && $(PY) decide routing && $(PY) decide siting && $(PY) decide anomaly \
	          && $(PY) decide scenarios && $(PY) decide climate
export:     ; $(PY) serve export && cp -r build/* web/data/
validate:   ; $(PY) validate all && pytest tests/ -q
demo:       ; cd web && python -m http.server 8080
clean:      ; rm -rf data/interim/* data/processed/* build/*
