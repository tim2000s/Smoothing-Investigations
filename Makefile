PY := python3
PATH := /opt/homebrew/opt/openjdk@21/bin:$(PATH)
RUNS := runs
REPORTS := reports
PAPERS := papers

.PHONY: all test fixtures parity_kotlin backtest analysis cohort \
        per_step_modify compare cross_smoother spectral phenotypes \
        sid_redetect deviation_plots phase2 meals upload_path papers \
        clean

all: test backtest analysis phase2 meals upload_path papers

cohort:
	$(PY) -m backtest.cli.run_backtest --tables oref_v5,oref_v6,oref_v7 \
		--cohort backtest/cohort.json --refresh-cohort

backtest:
	$(PY) -m backtest.cli.run_backtest --cohort backtest/cohort.json \
		--days 90 --out $(RUNS)

# Synthetic input fixtures the parity tests use as inputs to both the
# Kotlin reference driver and the Python ports.
fixtures:
	$(PY) -m backtest.tests.make_fixtures

# Build the Kotlin reference outputs (batch + online sliding-window for
# all three smoothers). Requires JDK 21 and Gradle on PATH.
parity_kotlin: fixtures
	cd backtest/reference/kotlin_driver && \
	  PATH=/opt/homebrew/opt/openjdk@21/bin:$$PATH \
	  gradle --no-daemon -q run \
	    --args="../../tests/fixtures/inputs.json ../../tests/fixtures/kotlin/"

test:
	$(PY) -m pytest backtest/tests -v

analysis: per_step_modify compare cross_smoother spectral phenotypes \
          sid_redetect deviation_plots

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

deviation_plots:
	$(PY) -m backtest.cli.normalised_deviation_plots --target all

phase2:
	$(PY) -m backtest.cli.phase2_run --out $(RUNS)/phase2
	$(PY) -m backtest.cli.phase2_analysis --runs $(RUNS)/phase2 \
	    --out $(REPORTS)/phase2

meals:
	$(PY) -m backtest.cli.meal_event_smoother_impact \
	    --data-dir data/nstest3 --out $(REPORTS)/meal_events

upload_path:
	$(PY) -m backtest.cli.upload_path_study --out $(REPORTS)/upload_path

# Render the four primary papers from markdown to DOCX.
papers:
	$(PY) -m backtest.cli.render_md_to_docx \
	    --input $(PAPERS)/01_cohort_backtest.md \
	    --output $(PAPERS)/01_cohort_backtest.docx \
	    --figure "$(REPORTS)/figs/pareto_noise_vs_delay.png:Pareto" \
	    --figure "$(REPORTS)/figs/transfer_function.png:Transfer function" \
	    --figure "$(REPORTS)/figs/per_step_modification.png:Per-step modification" \
	    --figure "$(REPORTS)/deviation_plots/calm_aligned_deviations.png:Calm-window deviations" \
	    --figure "$(REPORTS)/deviation_plots/rate_rise_aligned_deviations.png:Rate-rise deviations" \
	    --figure "$(REPORTS)/figs/sid_pareto.png:SID pareto"
	$(PY) -m backtest.cli.render_md_to_docx \
	    --input $(PAPERS)/02_sensor_g6_vs_g7.md \
	    --output $(PAPERS)/02_sensor_g6_vs_g7.docx \
	    --figure "$(REPORTS)/phase2/figs/per_sensor_noise_reduction_ratio.png:Noise reduction by sensor" \
	    --figure "$(REPORTS)/phase2/figs/per_sensor_phase_shift_delay_min.png:Phase shift by sensor" \
	    --figure "$(REPORTS)/phase2/figs/per_sensor_hypo_preserved_pct.png:Hypo preservation by sensor" \
	    --figure "$(REPORTS)/phase2/figs/per_sensor_outlier_absorbed_pct.png:Outlier absorption by sensor" \
	    --figure "$(REPORTS)/phase2/figs/within_user_g6_vs_g7.png:Within-user paired"
	$(PY) -m backtest.cli.render_md_to_docx \
	    --input $(PAPERS)/03_meal_event_impact.md \
	    --output $(PAPERS)/03_meal_event_impact.docx \
	    --figure "$(REPORTS)/meal_events/figs/meal_0000.png:Meal 1" \
	    --figure "$(REPORTS)/meal_events/figs/meal_0010.png:Meal 2" \
	    --figure "$(REPORTS)/meal_events/figs/meal_0020.png:Meal 3"
	$(PY) -m backtest.cli.render_md_to_docx \
	    --input $(PAPERS)/04_upload_path_disagreement.md \
	    --output $(PAPERS)/04_upload_path_disagreement.docx \
	    --figure "$(REPORTS)/upload_path/figs/User_D_upload_path_diagnostic.png:User_D" \
	    --figure "$(REPORTS)/upload_path/figs/User_L_upload_path_diagnostic.png:User_L"

clean:
	rm -rf $(RUNS)/* $(REPORTS)/*
	find . -name __pycache__ -type d -exec rm -rf {} +
