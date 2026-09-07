# NagarNetra AI Lab
#
# Everything runs in a container so the lab needs no Python on the host.
# macOS ships GNU Make 3.81, which has no .ONESHELL — recipes use `; \`.

SHELL := /bin/bash
COMPOSE := docker compose
RUN := $(COMPOSE) run --rm lab
VIDEO ?= /data/videos/road_cctv.mp4
CONFIG ?= default
PROFILE ?= core

.PHONY: help build models doctor run fast accurate sweep ablation report evaluate mine test lint shell clean footage validate

help:
	@echo "NagarNetra AI Lab"
	@echo ""
	@echo "  make build              build the lab image (PROFILE=core|torch)"
	@echo "  make models             download ONNX weights (~22 MB)"
	@echo "  make doctor             verify dependencies, engines and weights"
	@echo ""
	@echo "  make footage            generate synthetic footage with known plates"
	@echo "  make validate           end-to-end check against that known ground truth"
	@echo ""
	@echo "  make run                process VIDEO with CONFIG"
	@echo "  make fast               quick pass over VIDEO"
	@echo "  make accurate           accuracy-ceiling pass over VIDEO"
	@echo "  make sweep              default vs fast vs accurate, compared"
	@echo "  make ablation           prove consensus and the plate model earn their keep"
	@echo ""
	@echo "  make report             rebuild the HTML report for the latest run"
	@echo "  make evaluate GT=gt.csv measure the latest run against ground truth"
	@echo "  make mine               export hard cases as a fine-tuning dataset"
	@echo ""
	@echo "  make test               unit tests"
	@echo "  make lint               ruff"
	@echo "  make shell              a shell inside the lab container"
	@echo ""
	@echo "  VIDEO=$(VIDEO)"
	@echo "  CONFIG=$(CONFIG)  PROFILE=$(PROFILE)"

build:
	AILAB_PROFILE=$(PROFILE) $(COMPOSE) build

models:
	./scripts/fetch_models.sh

doctor:
	$(RUN) doctor

# Synthetic footage with known plates. The bundled sample clips have no legible
# plate, so this is what makes an end-to-end accuracy check possible at all.
footage:
	$(COMPOSE) run --rm --entrypoint python lab scripts/make_test_footage.py \
		--background /data/videos/road_cctv.mp4 \
		--vehicles /data/videos/road_cctv.mp4 \
		--out /lab/runs/_synthetic --vehicles-count 8 --seconds 16

validate: 
	@test -f runs/_synthetic/synthetic_traffic.mp4 || $(MAKE) footage; \
	$(RUN) run /lab/runs/_synthetic/synthetic_traffic.mp4 \
		--ground-truth /lab/runs/_synthetic/ground_truth.csv

run:
	$(RUN) run $(VIDEO) --config $(CONFIG)

fast:
	$(RUN) run $(VIDEO) --config fast

accurate:
	$(RUN) run $(VIDEO) --config accurate

sweep:
	$(RUN) sweep $(VIDEO) --configs default,fast,accurate

# The two ablations that answer "is this complexity justified?"
ablation:
	$(RUN) sweep $(VIDEO) --configs default,experiments/no_consensus,experiments/classical_plates

report:
	$(RUN) report latest

evaluate:
	@if [ -z "$(GT)" ]; then \
		echo "usage: make evaluate GT=path/to/ground_truth.csv"; exit 1; \
	fi; \
	$(RUN) evaluate latest --ground-truth $(GT)

mine:
	$(RUN) mine latest --out /lab/datasets/mined

test:
	$(RUN) --entrypoint pytest . -q

lint:
	$(RUN) --entrypoint ruff check ailab tests

shell:
	$(COMPOSE) run --rm --entrypoint bash lab

clean:
	rm -rf runs/*/ datasets/*/
	@echo "cleared runs/ and datasets/ (models kept)"
