PY := python3
PATH := /opt/homebrew/opt/openjdk@21/bin:$(PATH)
RUNS := runs
REPORTS := reports

.PHONY: all test fixtures parity_kotlin backtest analysis cohort \
        per_step_modify compare cross_smoother spectral phenotypes \
        sid_redetect paper notebook clean

all: test backtest analysis paper

cohort:
	$(PY) -m backtest.cli.run_backtest --tables oref_v5,oref_v6,oref_v7 \
		--cohort backtest/cohort.json --refresh-cohort

backtest:
	$(PY) -m backtest.cli.run_backtest --cohort backtest/cohort.json \
		--days 90 --out $(RUNS)

fixtures:
	$(PY) -m backtest.tests.make_fixtures
	$(PY) -m backtest.tests.make_python_ref

parity_kotlin: fixtures
	cd backtest/reference/kotlin_driver && \
	  PATH=/opt/homebrew/opt/openjdk@21/bin:$$PATH \
	  gradle --no-daemon -q run \
	    --args="../../tests/fixtures/inputs.json ../../tests/fixtures/kotlin/"

test:
	$(PY) -m pytest backtest/tests -v

analysis: per_step_modify compare cross_smoother spectral phenotypes sid_redetect

per_step_modify:
	$(PY) -m backtest.cli.per_step_modify --runs $(RUNS) --out $(REPORTS)

compare:
	$(PY) -m backtest.cli.compare --runs $(RUNS) --out $(REPORTS)

cross_smoother:
	$(PY) -m backtest.cli.cross_smoother \
		--per-user-metrics $(REPORTS)/per_user_metrics.csv --out $(REPORTS)

spectral:
	$(PY) -m backtest.cli.spectral --runs $(RUNS) --out $(REPORTS)

phenotypes:
	$(PY) -m backtest.cli.phenotypes \
		--per-user-metrics $(REPORTS)/per_user_metrics.csv --out $(REPORTS)

sid_redetect:
	$(PY) -m backtest.cli.sid_redetect --runs $(RUNS) --out $(REPORTS)

paper:
	$(PY) -m backtest.cli.paper --reports $(REPORTS) --out $(REPORTS)/paper.docx

notebook:
	papermill notebooks/analysis.ipynb $(REPORTS)/analysis_rendered.ipynb \
		-p runs_dir $(RUNS) -p reports_dir $(REPORTS)

clean:
	rm -rf $(RUNS)/* $(REPORTS)/*
	find . -name __pycache__ -type d -exec rm -rf {} +
