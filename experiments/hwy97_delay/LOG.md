# Experiment log: Hwy 97 delay forecasting

Append-only. One entry per run, including failed and negative results. Each entry must be reproducible from its git hash, and cites its **MLflow run ID** (local store `data/mlflow/`, experiment `hwy97_delay`; see HARNESS.md). MLflow holds the numbers, figures and predictions. This log holds the reasoning.

Scores are **development folds only** unless the entry is marked `LOCKED TEST` (owner-approved, at most once per candidate).

Links: issue numbers open the GitHub issue, whose **Experiment runs** section links back to its entries here. Git hashes open the commit on GitHub. MLflow run IDs open the run in the local MLflow UI, so start it first from the repo root: `conda run -n transit_env mlflow ui --backend-store-uri sqlite:///data/mlflow/mlflow.db`.

## Summary table

| ID | Date | Git | Hypothesis (issue) | Change | MAE (s) | Δ vs B0 [95% CI] | Δ vs best baseline | Verdict |
|---|---|---|---|---|---|---|---|---|
| E000 | 2026-11-19 | [56c993e](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/56c993e) | prior evidence | Segment 4 notebook, not run under the harness | see below | n/a | n/a | context |
| E001 | 2026-11-20 | [bb769cf](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/bb769cf) | H0 | dev: harmonic_huber ([7741d709320c49ae9da47ce907853ecd](http://127.0.0.1:5000/#/experiments/1/runs/7741d709320c49ae9da47ce907853ecd)) | 139.0 (B0 166.2) | -27.2 [-36.0, -18.6] (-16.4%) | -4.2 vs B3_calendar_median | provisional baseline |
| E002 | 2026-11-20 | [64f51eb](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/64f51eb) | H1 ([#13](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/13)) | dev: harmonic_huber ([f38bff5b31864f3497745333592f9ee2](http://127.0.0.1:5000/#/experiments/1/runs/f38bff5b31864f3497745333592f9ee2)) | 134.4 (B0 166.1) | -31.7 [-38.8, -24.7] (-19.1%) | -2.6 vs B3_calendar_median | H1 confirmed; superseded by E003 |
| E003 | 2026-11-20 | [5d9cfa5](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/5d9cfa5) | H1 ([#13](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/13)) | dev: harmonic_huber ([98ca4b712d70439eaf1fde2a555d32fe](http://127.0.0.1:5000/#/experiments/1/runs/98ca4b712d70439eaf1fde2a555d32fe)) | 134.5 (B0 166.2) | -31.7 [-38.8, -24.7] (-19.1%) | -2.5 vs B3_calendar_median | keep; current baseline |
| E004 | 2026-11-20 | [1fe6300](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/1fe6300) | H0 (evidence for H7, [#15](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/15)) | dev: harmonic_huber + traversal diagnostics ([a9a38c98ebe3421e8d0f8562c594fef5](http://127.0.0.1:5000/#/experiments/1/runs/a9a38c98ebe3421e8d0f8562c594fef5)) | 134.5 (B0 166.2) | -31.7 [-38.8, -24.7] (-19.1%) | -2.5 vs B3_calendar_median | diagnostic; see H7 |
| E005 | 2026-11-21 | [2ba991b](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/2ba991b) | H4 ([#17](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/17)) | dev: pooled_harmonic ([60dd6de41dd34ac1835385a63a510b98](http://127.0.0.1:5000/#/experiments/1/runs/60dd6de41dd34ac1835385a63a510b98)) | 165.1 (B0 166.2) | -1.0 [-7.3, +4.4] (-0.6%) | +28.1 vs B3_calendar_median | discard |
| E006 | 2026-11-21 | [714ceff](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/714ceff) | H4 ([#17](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/17)) | dev: pooled_harmonic_partial ([f4dabda4739742f99091f28fc7051fed](http://127.0.0.1:5000/#/experiments/1/runs/f4dabda4739742f99091f28fc7051fed)) | 146.4 (B0 166.2) | -19.8 [-25.5, -14.1] (-11.9%) | +9.4 vs B3_calendar_median | discard; H4 refuted |
| E007 | 2026-11-21 | [98ddf13](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/98ddf13) | H3 ([#18](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/18)) | dev: harmonic_recent_level ([b57247076a5a43e2a2ad5ebf8d14b4d3](http://127.0.0.1:5000/#/experiments/1/runs/b57247076a5a43e2a2ad5ebf8d14b4d3)) | 138.5 (B0 166.2) | -27.6 [-35.2, -20.1] (-16.6%) | +1.5 vs B3_calendar_median | discard; H3 refuted at this history length |
| E008 | 2026-11-22 | [7e8ec57](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/7e8ec57) | [#21](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/21) | dev: harmonic_huber ([9e031ab3b21441eb9a4bc84b5cb3a5fa](http://127.0.0.1:5000/#/experiments/1/runs/9e031ab3b21441eb9a4bc84b5cb3a5fa)) | 134.6 (B0 166.3) | -31.7 [-38.9, -24.8] (-19.1%) | -2.5 vs B3_calendar_median | keep; untracked journeys excluded, baseline unchanged |
| E009 | 2026-11-22 | [b838ef7](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/b838ef7) | H6 ([#14](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/14)) | dev: harmonic_huber + calendar_median anchor ([17781bc9f5d145fe93d8558d478515ee](http://127.0.0.1:5000/#/experiments/1/runs/17781bc9f5d145fe93d8558d478515ee)) | 136.3 (B0 166.3) | -30.0 [-38.0, -21.9] (-18.0%) | -4.6 vs B3_calendar_median | discard; bias removed (+2.8 s) but MAE +1.7 vs E008 |
| E010 | 2026-11-22 | [7980583](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/7980583) | [#12](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/12) | dev: harmonic_huber ([20ccac3e2f11424598521a00fc921fe5](http://127.0.0.1:5000/#/experiments/1/runs/20ccac3e2f11424598521a00fc921fe5)) | 134.6 (B0 166.3) | -31.7 [-38.8, -25.0] (-19.1%) | -1.8 vs B3_calendar_median | keep; stale first-stop departures train link 1 (+1,297 links), new reference baseline |
| E011 | 2026-11-22 | [650edbc](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/650edbc) | H8 ([#25](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/25)) | exploration only ([b010f8bef5fd41f986c91aa7b1584481](http://127.0.0.1:5000/#/experiments/1/runs/b010f8bef5fd41f986c91aa7b1584481)) | n/a | n/a | n/a | design and prediction registered; scored run pending |
| E012 | 2026-11-22 | [1d996ac](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/1d996ac) | [#13](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/13) | dev: harmonic_huber ([f458246392df4df39e7259c10dd71fc5](http://127.0.0.1:5000/#/experiments/1/runs/f458246392df4df39e7259c10dd71fc5)) | 134.6 (B0 166.3) | -31.7 [-38.8, -25.0] (-19.1%) | -1.8 vs B3_calendar_median | keep; #13 root cause corrected (stale tz database), scores identical to E010 |
| E013 | 2026-11-22 | [bb12d9b](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/bb12d9b) | anchor-free metric | dev: harmonic_huber ([eaec6ba932224a028e518e2fc0810ac7](http://127.0.0.1:5000/#/experiments/1/runs/eaec6ba932224a028e518e2fc0810ac7)) | 134.6 (B0 166.3; anchor-free 123.0, defective) | -31.7 [-38.8, -25.0] (-19.1%) | -1.8 vs B3_calendar_median | primary unchanged; anchor-free superseded by E014 |

## Entries

### E000: prior evidence (segment 4 harmonic notebook)

- Scope: one segment, traversal time (not delay), reserved week Nov 3–9, 391 visits. This week was already seen during EDA, so the result is exploratory.
- **Training period (in-sample):** the harmonic model beat Transit BC's earliest-feed traversal at capturing recurring traffic, particularly the **early-day/morning peak, which Transit BC under-predicts**. This is a fit diagnostic, not held-out skill.
- **Holdout (Nov 3–9, after Nov 1):** the **morning peak appears shifted** relative to the learned pattern, which degraded the harmonic model's performance:
  - Traversal MAE: early-feed implied (Transit BC) 1.022 min, harmonic Huber-Lasso 1.248 min, global training median 1.133 min.
  - Per visit: Transit BC had the lower error on 212 visits, harmonic on 179.
  - Observed daily medians sat below the fitted pattern after Nov 1.
- The shift coincides with the DST change on Sun 2026-11-01. A timezone (phase) bug vs a genuine timing/level change is untested: backlog H1.
- Implication: there is a recurring morning peak Transit BC misses, which supports the study. But the pattern must survive changes like Nov 1, and the B2/B3 medians are serious competitors.

### E001: harness baseline (H0), 2-month window

- Hypothesis: H0, i.e. build the harness and reproduce B0. Candidate is the segment 4 notebook's harmonic Huber-Lasso (daily 6, weekly 3, alpha 0.003, delta 0.15), fitted per segment.
- Prediction before running: none recorded (this run builds the harness).
- Change: new harness at bb769cf. Data is the 2026-09-11 to 11-10 snapshot; 6 weekly dev folds from Sep 28 to Nov 8; stale final reports excluded.
- Git: [bb769cf](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/bb769cf)   MLflow run: [7741d709320c49ae9da47ce907853ecd](http://127.0.0.1:5000/#/experiments/1/runs/7741d709320c49ae9da47ce907853ecd)
- Results (dev, 27,759 visits, 40 service days; holidays scored separately):
  - MAE: B0 166.2 s, B1 166.2, B2 240.4, B3 143.2, **harmonic 139.0**, shuffled control 242.0.
  - Harmonic vs B0: −27.2 s [−36.0, −18.6], −16.4%, win rate 0.62. Beats B3 by 4.2 s overall and in 5 of 6 folds (loses in dev4).
  - Per fold, harmonic MAE ranges 124 to 152 s.
- Sanity checks: leakage assertions passed; all methods scored on identical rows; shuffled control ≥ B2 (passed).
- Observations:
  - B0 is the timetable (always 0 delay), so B0 = B1.
  - Every method predicts too early (bias −53 to −138 s).
  - Largest errors are in the PM peak.
  - In the 8–9 AM peak, harmonic ≈ B0.
  - **dev6 (Nov 2–8): harmonic 144.4 and B3 152.6 vs B0 141.7**, the only fold where the calendar models lose.
- Data defects found afterwards (both affect this run):
  1. Stored timestamps are 1 h early after the DST change on 2026-11-01 (all 160 trips seen both before and after shift by exactly −60 min in local time). This explains dev6 and the "peak shift".
  2. Untracked journeys whose final delays are all exactly 0 (93 journeys, including all of Nov 1) are still scored, which flatters B0.
- Verdict: **provisional baseline.** The harness works and the candidate clears the criterion on dev data. But dev6 runs on mis-timed data, and B3 is a close competitor, so most of the gain may come from the dynamics identity plus a time-of-day lookup rather than the harmonic form. Re-run after the timestamp correction.
- Next: fix timestamps (owner decision), decide on the untracked-journey rule, then H2 (history back to January).

### E002: timestamp correction (H1)

- Hypothesis: H1. The post-Nov-1 morning-peak "shift" is a timestamp defect, not a traffic change.
- Prediction before running: dev6 (Nov 2–8) calendar-model MAE drops to the level of the other folds and beats B0; other folds barely change.
- Change: temporary timestamp correction at 64f51eb (+1 h for rows written from 2026-11-02 08:00 stored time) plus the `schedule_clock_mismatch` exclusion (net 75 fewer scored visits than E001). Root cause tracked in issue #13. Same model and folds as E001.
- Git: [64f51eb](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/64f51eb)   MLflow run: [f38bff5b31864f3497745333592f9ee2](http://127.0.0.1:5000/#/experiments/1/runs/f38bff5b31864f3497745333592f9ee2)
- Results (dev, 27,684 visits, 40 service days):
  - MAE: B0 166.1 s, B2 240.5, B3 137.0, **harmonic 134.4**.
  - Harmonic vs B0: −31.7 s [−38.8, −24.7], **−19.1%**, win rate 0.63.
  - **dev6 (Nov 2–8), E001 → E002:** harmonic 144.4 → 118.9 s, B3 152.6 → 118.0 s, B0 141.7 → 140.9 s. Folds dev1–dev5 are unchanged to within 0.4 s.
  - AM peak: harmonic 154.3→140.4 s (B0 160.0). PM peak: 171.1 (B0 231.1).
- Sanity checks: all months agree ≥ 98.5% with start descriptors; leakage assertions passed; identical rows; shuffled control 239.2 ≥ 0.99 × B2 (passed).
- Verdict: **H1 confirmed (keep).** The peak shift came from the timestamp defect. With correct clocks the calendar models beat B0 in every fold, including Nov 2–8. This is the new baseline. Caveat: harmonic beats B3 by only 2.6 s and loses to it in dev4 and dev6, so the evidence for the harmonic form over a plain calendar lookup is weak.
- Next: H2 (history back to January; check that months' schedule-clock agreement first), and H6 (first-stop departure delay; every method is still biased 55–140 s early).

### E003: proper clock fix, timetable calendar clock (H1)

- Hypothesis: H1 follow-up. The E002 gain comes from keying calendar features to the trip's timetable, not from moving event times.
- Prediction before running: the same MAE as E002 (within about 1 s), with no visits lost to clock exclusions.
- Investigation (issue #13, root cause):
  - Transit BC's feed, not our storage. The R2 archive matches the direct MySQL download row for row.
  - The live feed shows the same −60 min offset between its schedules and both the start descriptor and the static GTFS.
  - The header clock matches real time (checked against Google's and tmix's HTTP `Date` headers).
  - Vehicle GPS is at the stop at the reported arrival time (median 52 m, 95% within 300 m, same as before Nov 2).
  - So event times are physical, and the **trip labels** point to the timetable an hour later.
  - Summer 2026 samples show offset 0.
  - The archive has no data from Feb to May 2026; route-tagged data starts around June/July.
  - Note: two live-feed checks on 2026-11-19 fall inside the locked-test period. They were used only for clock relationships: nothing was fitted, scored or plotted, and one bus's delay value was printed.
- Change: 5d9cfa5. `forecasting/feed_clock.py` measures each journey's whole-hour offset to its descriptor. Event times are never shifted. Service days and the models' `scheduled_departure` use the timetable clock (`calendar_clock: "timetable"`). Journeys with non-whole-hour offsets are excluded (48, all in September). This replaces E002's window shift, which also moved `feed_timestamp`.
- Git: [5d9cfa5](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/5d9cfa5)   MLflow run: [98ca4b712d70439eaf1fde2a555d32fe](http://127.0.0.1:5000/#/experiments/1/runs/98ca4b712d70439eaf1fde2a555d32fe)
- Results (dev, 27,759 visits, the same rows as E001):
  - MAE: B0 166.2 s, B2 240.5, B3 137.0, **harmonic 134.5**.
  - Harmonic vs B0: −31.7 s [−38.8, −24.7], −19.1%, win rate 0.63.
  - dev6: harmonic 119.6, B3 118.2, B0 141.7.
- Sanity checks: monthly offsets are Sep 98.9% / Oct 99.9% at 0, and Nov 92.7% at +60, 7.3% at 0 (Nov 1 and early Nov 2). Leakage assertions passed; shuffled control passed.
- Verdict: **keep. This is the current baseline.** It confirms the clock interpretation. Harmonic vs B3 is still only −2.5 s overall, and harmonic loses to B3 in dev4 and dev6.
- Next: H6 (first-stop departure delay; bias −55 to −140 s), and H2 with the realistic history (about June/July onwards).

### E004: per-segment traversal and dwell diagnostics

- Hypothesis: diagnostic (owner request). Is traversal predicted well even where delay is not? The owner expected segment 14's traversal to look better than its delay.
- Change: 1fe6300. The harness now scores traversal and dwell per method and segment, logs them to MLflow (`traversal_*`, `dwell_*`, `traversal_segment_MAE_s/*`) and saves `traversals.csv.gz`. New figures: a segment-vs-stop error overview, plus a 3-row time series (traversal, upstream dwell, downstream delay) and a predicted-vs-observed traversal scatter for each segment. Transit BC's implied traversal and dwell are its timetable run time and scheduled dwell. The predictions are identical to E003. The run is marked DIRTY only because of an unrelated, uncommitted owner edit to `notebooks/dynamics_model.ipynb`.
- Git: [1fe6300](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/1fe6300)   MLflow run: [a9a38c98ebe3421e8d0f8562c594fef5](http://127.0.0.1:5000/#/experiments/1/runs/a9a38c98ebe3421e8d0f8562c594fef5)
- Results (dev, 26,811 traversals, holidays excluded):
  - **Traversal MAE:** B0 timetable 65.1 s (bias +18.8), B2 42.3, B3 35.5, **harmonic 33.7** (bias −1.2), shuffled control 42.6.
  - **Dwell:** scheduled (B0) 24.9 s MAE (bias −23.1); stop median 15.0 s (bias −4.0).
  - **By segment:** harmonic ranges 22.5 s (seq 4) to 67.2 s (seq 6, 103745→102853, the notebook's "segment 4"). Seq 14 is 25.3 s vs 62.5 s for B0; seq 2 is 35.1 s.
  - **Delay MAE by stop** grows from about 1.2 min (stop 2) to 3.3 min (stop 14). At stops 13–14, B0 (3.2 min) matches our methods, even though our traversal errors are half of B0's.
- Interpretation: **the owner's expectation holds.** Segment 14's traversal is predicted well (0.42 min). Its delay looks worse because the delay at stop 14 accumulates the errors of 13 chained segments, plus the anchor error. The accumulation, and B0 catching up at the end, suggest errors that are correlated within a journey and/or schedule recovery (buses holding at timepoints), which a plain sum of traversals ignores.
- Verdict: diagnostic only; no change to the baseline.
- Next: H6 (anchor departure delay), and H7 (recovery-aware chaining).

### E005: pooled harmonic traversal, shared calendar shape (H4, #17)

- Hypothesis: H4. A pooled model (per-segment robust level plus **one** harmonic calendar shape shared by all segments) beats per-segment harmonic models, especially on low-support segments.
- Prediction before running: **no overall gain.** Almost every segment has thousands of training links, so the per-segment fallback rarely applies, while a fully shared shape loses segment-specific peaks. Expected traversal MAE 34–37 s (harmonic 33.7), worst on seq 6 (the notebook's segment 4); delay MAE 134–139 s (harmonic 134.5, B3 137.0). Gains, if any, only on low-support segments with few rows.
- Pre-declared follow-up (run only if E005 loses on segments with their own peaks): `pooled_harmonic_partial`, the same model plus per-segment deviations from the shared shape at L1 `alpha=0.01` (shared shape `0.003`). Predicted within ±1.5 s of harmonic delay MAE. No other variants or tuning.
- Note: H6, H7, H2 and H5 (higher or equal priority) are blocked because they need harness changes (see their tickets), so H4 runs first.
- Change: `PooledHarmonicTraversal` in `traversal_models.py`, registered as `pooled_harmonic` and `pooled_harmonic_partial`; unit tests in `tests/unit/test_traversal_models.py`. No harness change.
- Git: [2ba991b](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/2ba991b) (clean)   MLflow run: [60dd6de41dd34ac1835385a63a510b98](http://127.0.0.1:5000/#/experiments/1/runs/60dd6de41dd34ac1835385a63a510b98)
- Results (dev, 27,759 visits on 40 service days, the same rows as E003/E004; 0 unscorable for every method):
  - **Delay MAE:** B0 166.2 s, B2 240.5, B3 137.0, **pooled 165.1** (harmonic was 134.5 in E003/E004).
  - Pooled vs B0: −1.0 s [−7.3, +4.4], −0.6%, win rate 0.54. **+28.1 s vs B3.**
  - **Bias:** pooled −98.9 s, vs B3 −54.7 s (harmonic −70.8 s in E003; first written here as "about −55 s", corrected from MLflow during E007).
  - **By fold:** pooled is worse than B3 in all six (e.g. dev1 186.7 vs 158.1, dev4 161.0 vs 125.0, dev6 144.5 vs 118.2).
  - **By period:** am_peak 199.8 (B3 140.0), pm_peak 238.4 (B3 168.1); evening 99.8 is level with B3 (100.4).
  - **Traversal MAE:** pooled 38.4 s (bias −5.2) vs B3 35.5, harmonic 33.7 (E004).
  - **By segment (pooled vs B3):** much worse on seq 10 (61.6 vs 43.2), seq 11 (42.3 vs 33.1) and seq 6 (80.1 vs 75.3). Level or slightly better on seq 4, 8 and 9.
- Sanity checks: leakage assertions passed; identical rows across methods; shuffled control passed (240.0 vs B2 240.5).
- Interpretation: **the prediction held, and the loss was larger than expected.** One shared shape can't represent peaks that only some segments have (seq 6, 10, 11 slow down in the peaks; others barely do). Those segments are under-predicted at the same times in the same journeys, so the errors add up along the chain. A 4.7 s traversal loss becomes a 30 s delay loss, with a bias of −99 s. Low-support segments (the reason for pooling) carry almost no rows on this route.
- Verdict: **discard.** Running the pre-declared partial-pooling follow-up (E006) because the loss is concentrated on segments with their own peaks.

### E006: pooled harmonic with per-segment deviations (H4 follow-up, #17)

- Hypothesis: H4 refine. Per-segment deviations from the shared shape, shrunk towards it (L1 `alpha=0.01` vs `0.003` for the shape), keep the segment-specific peaks while borrowing strength for thin cells.
- Prediction before running (declared in E005): delay MAE within ±1.5 s of harmonic (134.5 s), i.e. about 133–136 s. Not expected to beat B3 by a meaningful margin.
- Change: none; `pooled_harmonic_partial` was committed with E005 (2ba991b).
- Git: [714ceff](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/714ceff) (clean)   MLflow run: [f4dabda4739742f99091f28fc7051fed](http://127.0.0.1:5000/#/experiments/1/runs/f4dabda4739742f99091f28fc7051fed)
- Results (dev, the same 27,759 visits; 0 unscorable):
  - **Delay MAE:** B0 166.2 s, B3 137.0, **partial 146.4** (harmonic 134.5, pooled 165.1).
  - Partial vs B0: −19.8 s [−25.5, −14.1], −11.9%, win rate 0.60. **+9.4 s vs B3**; loses to B3 in all six folds (dev1 163.7 vs 158.1 … dev4 141.6 vs 125.0).
  - **Bias:** −84.8 s (B3 −54.7).
  - **By period:** am_peak 175.5 (B3 140.0) and pm_peak 192.9 (B3 168.1) carry the loss. Early, midday and evening are level with B3.
  - **Traversal MAE:** 35.0 s (bias −3.0), slightly better than B3 (35.5) but worse than harmonic (33.7). Better than B3 on 11 of 13 segments; worse on seq 10 (49.2 vs 43.2) and seq 14.
- Sanity checks: leakage assertions passed; identical rows; shuffled control passed (239.6 vs B2 240.5).
- Interpretation: **prediction failed** (expected 133–136 s). Shrinking segment shapes towards the shared shape still flattens the peaks of seq 6, 10 and 11 a little. A −3 s per-link traversal bias concentrated in peak hours becomes about −30 s of extra delay bias after 13 chained links. That's why a model that beats B3 on traversal MAE loses to it on delay MAE. For chained forecasts, **per-link bias at the same time of day matters more than per-link MAE**.
- Verdict: **discard; H4 refuted on this route.** Per the pre-registration, no further variants. Every segment of Route 97 direction 0 has more than 1,200 dev traversals, so pooling has nothing to rescue. Harmonic per segment (E003) stays the baseline.
- Next: H3 (#18). H6/H7 would act directly on the accumulated bias but need harness changes (see #14, #15).

### E007: harmonic plus a trailing 14-day level offset (H3, #18)

- Hypothesis: H3. A recent-level term tracks level changes without chasing incidents. Design: fit `harmonic_huber` on all training links, then scale each segment by `exp(median log residual)` over links in the last 14 days of training data (no offset if a segment has fewer than 30 recent links). The median ignores short incidents.
- Prediction before running: **inconclusive, within ±2 s of harmonic (134.5 s).** dev1–dev2 have only about 2–3 weeks of training, so the window covers most of it and the in-sample offset is about 0. Any gain should show in dev4–dev6. H2 (longer history), which the ticket calls the setting where this matters most, is blocked, so the expected effect is small. A real gain would also show as a smaller per-segment bias (see the E006 lesson).
- Single run; no window or threshold tuning on dev.
- Change: `RecentLevelTraversal` wrapper in `traversal_models.py`, registered as `harmonic_recent_level`; tests in `tests/unit/test_traversal_models.py`. No harness change.
- Git: [98ddf13](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/98ddf13) (clean)   MLflow run: [b57247076a5a43e2a2ad5ebf8d14b4d3](http://127.0.0.1:5000/#/experiments/1/runs/b57247076a5a43e2a2ad5ebf8d14b4d3)
- Results (dev, the same 27,759 visits; 0 unscorable):
  - **Delay MAE:** **138.5 s** vs harmonic 134.5 (E003), B3 137.0, B0 166.2.
  - vs B0: −27.6 s [−35.2, −20.1], −16.6%, win rate 0.62. **+1.5 s vs B3, +4.0 vs harmonic.**
  - **By fold, vs harmonic (E003):** worse in all six, with the gap growing where the window holds less of the training data: dev1 152.3 vs 151.9, dev2 138.5 vs 136.9, dev3 126.2 vs 124.1, dev4 132.3 vs 127.3, dev5 158.2 vs 149.6, dev6 125.7 vs 119.6.
  - **Bias:** delay −83.7 s vs harmonic −70.8 (E003). Traversal −2.4 s vs harmonic −1.2.
  - **Traversal MAE:** 33.4 s, the best of any method so far (harmonic 33.7, B3 35.5). Better than B3 on 12 of 13 segments, e.g. seq 6 65.0 vs 75.3.
- Sanity checks: leakage assertions passed; identical rows; shuffled control passed (239.6 vs B2 240.5).
- Interpretation: **prediction failed in direction.** I expected small gains in later folds; instead later folds lose most. The recent median log residual is mostly negative: the median of right-skewed traversals sits below the Huber fit. So the offset makes the *typical* link more accurate (best traversal MAE) but lowers the *sum*, and delay is a sum of 13 links. This is the third result (with E006) where a model that improves traversal MAE but adds negative traversal bias loses on delay.
- Verdict: **discard; H3 refuted at the current 2-month history.** The ticket expected it to matter most with longer history (H2, blocked). That case remains untested.
- Stop rule: E005, E006 and E007 are three consecutive runs without beating the best baseline (B3), so the session stops here, per the skill. 3 of 10 scored runs used.
- Next: proposed **H8** (RESEARCH.md backlog): make traversal forecasts *mean*-unbiased, since chained delay sums them. H6 (anchor, #14) attacks the larger part of the same bias, about −100 s at the first stop, but needs a harness change.

### E008: baseline rerun with untracked journeys excluded (#21)

- Purpose: owner decision on #21. Journeys whose final reported delays are all exactly 0 s are untracked and excluded from scoring and training (`untracked_journey`). Rerun the current baseline (harmonic, E003) and the ladder on the new truth.
- Prediction before running: dev rows drop by a few hundred visits (93 of 3,336 journeys are untracked, and the stale-report rule already removes many of their visits). B0 loses rows where it is exactly right (0 vs 0), so B0 MAE rises by about 1–4 s. Harmonic and B3 change by less than 2 s. The ranking (harmonic ≈ B3 < B0) stays the same.
- Change: `untracked_journey` exclusion in `prepare_origin_visits` (harness change approved by the owner on #21), with a unit test.
- Git: [7e8ec57](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/7e8ec57) (clean; code change in [be28f72](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/be28f72))   MLflow run: [9e031ab3b21441eb9a4bc84b5cb3a5fa](http://127.0.0.1:5000/#/experiments/1/runs/9e031ab3b21441eb9a4bc84b5cb3a5fa)
- Audit: 1,162 visits and 1,162 training links are flagged `untracked_journey`. The stale-report rule had already removed almost all of them: eligible scored visits fall from 39,973 (E007) to 39,916 (−57) and `stale_final_report` from 1,258 to 235; training links fall from 38,633 to 38,596 (−37).
- Results (dev, **27,742** visits, −17 vs E003–E007; 0 unscorable for every method):
  - **Delay MAE:** B0 166.3 s (was 166.2), B2 240.4 (240.5), B3 137.1 (137.0), **harmonic 134.6** (134.5).
  - Harmonic vs B0: −31.7 s [−38.9, −24.8], −19.1%, win rate 0.63 (unchanged). −2.5 s vs B3 (unchanged).
  - **By fold (harmonic / B3):** dev1 152.2 / 158.6, dev2 137.0 / 144.7, dev3 124.2 / 127.4, dev4 127.2 / 124.9, dev5 149.6 / 152.1, dev6 119.6 / 118.1. Harmonic still loses to B3 in dev4 and dev6.
  - **Bias:** B0 −138.4 s, B3 −55.1, harmonic −71.2. **Traversal MAE:** harmonic 33.7 s (bias −1.3), B3 35.5; unchanged.
- Sanity checks: leakage assertions passed; identical rows across methods; shuffled control passed (239.4 vs B2 240.4).
- Interpretation: **prediction failed in size, not direction.** B0 rose, as expected, but by 0.1 s, not 1–4 s, because the 10-minute stale rule (#12) had already caught 95% of untracked visits. The exclusion is now explicit, so it no longer depends on those reports being published early, but it doesn't change any conclusion.
- Verdict: **keep (truth definition).** All dev numbers from E008 on use it; E003–E007 differ by at most 0.1 s, so their comparisons stand. Harmonic (E008: 134.6 s) remains the baseline.
- Next: H6 (#14) needs the owner to approve its harness change; otherwise H8 (#25), judged on per-segment × hour traversal bias.

### E009: learned first-stop anchor (H6, #14)

- Hypothesis: H6. First-stop departure delay has a recurring calendar pattern, so a learned anchor (first stop × weekday × hour median of observed departure delay, trained per fold before the cutoff) lowers delay MAE versus Transit BC's earliest estimate, which is 0 s for 99.6% of journeys.
- Harness change approved by the owner (2026-11-21, "start working on H6"): opt-in `--anchor`, default `feed` reproduces E008. B0/B1 keep the feed anchor; B2, B3 and the candidate use the learned one. Training departures use the stale rule and exclude untracked journeys (#21).
- Diagnostic before running (E008 dev predictions, no new scoring): harmonic's delay bias is not a constant anchor offset. It is −44 s at stop 2, about −37 s at stops 3–6 and grows to about −100 s at stops 11–14. Fresh first-stop departures run a median 61 s late. A constant +40 s shift of E008 harmonic would cut MAE by only about 3 s; +60 s by about 1 s.
- Prediction before running: **small gain at best.** The learned anchor shifts forecasts by roughly +50 to +90 s, which overshoots stops 2–6 (bias turning positive) and helps stops 9–14. Harmonic delay MAE 130–136 s (E008: 134.6), overall bias −20 to +10 s (E008: −71.2). B3 moves similarly, so harmonic vs B3 stays within ±2 s of −2.5. The ticket's success bar (beating −19.1% vs B0 with most folds improving) is met only if the calendar variation in the anchor adds more than the constant shift. Single run, no tuning.
- Change: `anchor_models.py` (`CalendarMedianAnchor`, `make_anchor_model`), `first_stop_departures` and `learned_anchor` in the harness, `--anchor` in `evaluate.py`; unit tests.
- Git: [b838ef7](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/b838ef7) (clean)   MLflow run: [17781bc9f5d145fe93d8558d478515ee](http://127.0.0.1:5000/#/experiments/1/runs/17781bc9f5d145fe93d8558d478515ee)
- Results (dev, the same 27,742 visits as E008; 0 unscorable for every method):
  - **Delay MAE:** B0 166.3 s, B2 228.4 (E008: 240.4), B3 140.9 (137.1), **harmonic 136.3** (134.6).
  - Harmonic vs B0: −30.0 s [−38.0, −21.9], −18.0%, win rate 0.57 (E008: −31.7, −19.1%, 0.63). −4.6 s vs B3.
  - **By fold, harmonic learned vs feed anchor (E008):** dev1 156.0 vs 152.2, dev2 140.2 vs 137.0, dev3 128.5 vs 124.2, dev4 127.5 vs 127.2, dev5 145.3 vs 149.6, dev6 122.9 vs 119.6. Worse in 5 of 6.
  - **Bias:** harmonic +2.8 s (E008: −71.2), B3 +18.9 (−55.1). MedAE 98.1 vs 86.2 (worse), RMSE 198.8 vs 209.1 (better).
  - **By stop (harmonic bias, learned vs feed):** stop 2 +29 vs −44, stops 3–6 +31 to +48 vs −27 to −42, stop 9 −1 vs −75, stops 11–14 −22 to −32 vs −96 to −107. MAE is 14–19 s worse at stops 2–6 and 6–13 s better at stops 10–14.
  - **Anchor:** predicted first-stop departure delay mean 73 s, median 64 (P10–P90 34–119).
  - Traversal and dwell scores unchanged (the anchor only moves chained delay).
- Sanity checks: leakage assertions (including the new anchor ones) passed; identical rows; shuffled control passed (228.0 vs B2 228.4).
- Interpretation: **prediction mostly held:** bias removed, no MAE gain (harmonic 136.3 s, just outside the predicted 130–136; B3 lost more than expected, so harmonic vs B3 widened to −4.6). Two lessons:
  1. **The learned anchor is about 30 s too late.** With the feed anchor, stop 2 is 44 s early, so the effective departure lag is about 44 s, yet the anchor predicts 73 s on average. Fresh first-stop departure reports skew late: when a bus leaves on time, the feed seems to leave its 0 s estimate in place, so the report is stale and excluded from training. This is the selection bias suspected before the run.
  2. **Most of the early bias builds up along the chain, not at the anchor.** Harmonic's bias falls from −44 s at stop 2 to about −100 s at stop 14. That roughly matches the per-link mean biases: dwell rule −4.0 s × 12 stops plus traversal −1.3 s × 13 links ≈ −65 s. A single anchor shift can't fix a bias that grows with stop number. It trades early-stop error for late-stop error.
- Verdict: **discard (calendar-median anchor); H6 refuted in this form.** Keep the `--anchor` option (default `feed`) for future anchor models. Harmonic with the feed anchor (E008: 134.6 s) stays the baseline.
- Next: proposed **H9** (dwell mean bias, needs a harness change) and H8 (#25, traversal mean bias) attack the growing part of the bias. A refined anchor would need training data free of the stale-report selection, e.g. implied departures from fresh stop-2 arrivals.

### E010: stale first-stop departures train the first link (truth fix)

- Purpose: owner-approved harness fix (2026-11-21). The stale-report rule also dropped training links whose **upstream** departure report was stale. At the first stop, that removed about 40% of journeys from stop 1 → 2 training, mostly on-time leavers.
- Diagnostic before the change (dev window, not a scored run): vehicle positions as an independent check. Departure from stop k = first ping past k (bracketed by the previous ping, gaps ≤ 60 s); arrival = first ping `STOPPED_AT` k or past k.
  - **Method check, fresh reports:** arrivals agree with vehicles within 30 s / 60 s in 97% / 99.9% (n = 36,252); departures 88% / 98% (n = 38,136); stop-1 departures 52% / 88% (median feed − vehicle −28 s).
  - **Stale stop-1 departures** (1,312; 99.8% report exactly 0 s): vehicles show 99% left between −60 and +120 s (median 24 s late); the 0 s value is within 60 s of the vehicle in 91%, at least as good as fresh stop-1 reports. **Wrongly dropped.**
  - **Stale reports at stops 2–14** (318 arrivals, 296 departures; about 0.8%): only 53 each have pings, and **none** is within 60 s of the vehicle (median error about 26 min, some about 1 h, likely the #13 trip-label offset). Even the 0 s ones are wrong. **Correctly dropped.**
- Change: in `prepare_origin_visits`, the stale rule no longer applies to the upstream departure when it is the journey's first stop. Scored visits are unchanged (they only check the arrival report). Unit test added.
- Prediction before running: training links +about 1,280 (`stale_final_report` 1,567 → about 290), and stop 1 → 2 traversal-scoring rows 1,285 → about 2,150. Scored delay rows unchanged (27,742). On-time leavers take slightly less time on that link (stop-2 delay minus departure delay: median 66 s vs 73 s), so link-1 forecasts drop by a few seconds and every chained forecast moves slightly earlier. Delay bias becomes 2–5 s more negative, and harmonic delay MAE changes by −1 to +3 s (E008: 134.6). This is a correctness fix; keep it whatever the score.
- Git: [7980583](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/7980583) (clean)   MLflow run: [20ccac3e2f11424598521a00fc921fe5](http://127.0.0.1:5000/#/experiments/1/runs/20ccac3e2f11424598521a00fc921fe5)
- Audit: training links 38,596 → **39,893** (+1,297); `stale_final_report` among training links 1,567 → 270. Scored visits unchanged (39,916 eligible, 235 stale).
- Results (dev, the same **27,742** visits as E008; 0 unscorable):
  - **Delay MAE:** B0 166.3 s, B2 240.8 (E008: 240.4), B3 **136.4** (137.1), **harmonic 134.6** (134.6).
  - Harmonic vs B0: −31.7 s [−38.8, −25.0], −19.1%, win rate 0.64. **−1.8 s vs B3** (E008: −2.5).
  - **By fold (harmonic / B3):** dev1 152.1 / 159.7, dev2 136.5 / 143.1, dev3 124.5 / 124.4, dev4 127.4 / 124.5, dev5 150.2 / 152.3, dev6 119.4 / 117.8. Harmonic moved by at most 0.6 s per fold.
  - **Bias:** harmonic −74.0 s (E008: −71.2), B3 −58.9 (−55.1).
  - **Traversal:** scored links 26,800 → 27,703. Stop 1 → 2 (seq 2) rows 1,285 → 2,168; its MAE: harmonic 34.4 s (35.1), B3 37.6 (39.7). Overall harmonic 33.7 s (bias −1.0).
- Sanity checks: leakage assertions passed; identical rows; shuffled control passed (240.1 vs B2 240.8).
- Interpretation: **prediction held.** The row counts landed as predicted (+1,297 links, seq 2 at 2,168 rows). Bias moved 2.8 s earlier (predicted 2–5 s), and harmonic delay MAE didn't change (predicted −1 to +3 s). B3 gained 0.7 s. The stop 1 → 2 model now learns from on-time leavers too and scores better on that link. The score barely moves because the early bias that remains comes from the anchor, which is out of scope, and from per-link mean bias (H8, H9).
- Verdict: **keep (truth fix).** E010 replaces E008 as the reference baseline: harmonic 134.6 s, B3 136.4 s.
- Next: H8 (#25) or H9 (mean dwell, needs a harness change). Both are measured against E010.

### E011: exploration for H8 (mean-unbiased traversal, #25); scored run pending

- Hypothesis: H8. Chained delay needs mean-unbiased traversal forecasts; a per-segment × hour correction from training residuals lowers delay MAE.
- Exploration (first use of the `experiment-plots` skill), on E010's dev artifacts only. Exploration run: [b010f8bef5fd41f986c91aa7b1584481](http://127.0.0.1:5000/#/experiments/1/runs/b010f8bef5fd41f986c91aa7b1584481) (9 figures, 3 of them corrected v2s; the question → observation → next trail is in `journal.md`).

  1. **Where the bias comes from.** Harmonic's delay bias (−47 s at stop 2 → −104 s at stop 14) is a flat anchor offset of −49 to −58 s, plus dwell bias in **two steps**: −28 s from the dwell at stop 6 and −22 s from the dwell at stop 10. Cumulative traversal bias wanders between +24 and −10 s and nets to −9.5 s.

     ![Bias decomposition by stop](figures/E011/02_bias_decomposition_by_stop_v2.png)

  2. **Why stops 6 and 10:** timepoint holding. Early buses wait out the schedule, so dwell ≈ constant − arrival delay (slope −1). At stop 6, 37% of buses arrive > 30 s early (median dwell 151 s vs 62 s); at stop 10, 15% (75 s vs 30 s). Mean ≈ median dwell at every other stop. A constant mean dwell (proposed H9) would over-hold late buses and under-hold early ones. This is H7's mechanism (#15).

     ![Dwell vs arrival delay at stops 6 and 10](figures/E011/05_dwell_vs_arrival_delay_stops_6_10_v2.png)

  3. **Traversal bias is structured, mixed in sign, and only partly stable.** In the 13–17 h peak, links → 2 and → 6 are too slow by +25 to +40 s, and links → 8 to 10 too fast by −20 to −40 s; 60 of 243 segment × hour cells exceed ±10 s. Across folds, links → 2 (+16 to +33 s) and → 8 (−14 to −20 s) are stable. Link → 6 shrinks from +75 to +24 s as history grows. Links → 9 and → 10 swing from −73 to +19 s. A smearing ratio (right-skew correction) would push every link up and make links 2 and 6 worse.

     ![Peak-hour traversal bias by fold](figures/E011/07_pm_peak_bias_by_fold.png)

  4. **The anchor confounds delay MAE.** With the out-of-scope anchor error removed (actual stop-1 departure reconstructed as the stop-2 arrival minus the actual stop 1 → 2 traversal), harmonic delay MAE is 117.7 s (scored 135.7) with bias −14.6 s (scored −68.8); B3 is 121.6 s (138.2), bias +0.6 s. A per-link fix that removes positive traversal bias (e.g. link 2) moves forecasts earlier, on top of the anchor offset, so it can score worse while being better within scope.

     ![Delay MAE with the anchor error removed](figures/E011/09_delay_mae_anchor_free_by_stop_v2.png)

- Design decided from the plots: H8 as an **additive** per-segment × hour residual correction (training mean of actual − harmonic per cell, shrunk towards the segment mean for cells with few links), not a smearing ratio. Judge it first on traversal bias by segment × hour and traversal MAE, and report delay MAE both as scored and anchor-free (exploratory).
- Prediction (pre-registered, before any H8 code): traversal MAE 33.7 → 32.5–33.5 s, with the stable cells (→ 2, → 8 in the peak) close to 0. Scored delay MAE −2 to +2 s vs E010 (134.6): removing link 2's positive bias adds to the anchor offset. Anchor-free delay MAE improves by 1–4 s. The unstable links (→ 9, → 10) stay biased.
- Git: [650edbc](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/650edbc) (exploration code)   MLflow run: scored run pending; exploration [b010f8bef5fd41f986c91aa7b1584481](http://127.0.0.1:5000/#/experiments/1/runs/b010f8bef5fd41f986c91aa7b1584481)
- Next: implement and score H8 when the owner agrees. The plots point to bigger levers: **H7** (holding at stops 6 and 10; needs a harness change) and an **anchor-free delay metric** to match the scope decision (owner decision; changes the success criterion).

### E012: issue #13 root cause corrected (stale timezone database)

- Note: logged as E011 in PR #32; renumbered to E012 on merge because E011 (the H8 exploration) was already taken. Its MLflow `log_id` tag was updated to match.

- Purpose: correction, not a hypothesis test. **The explanation in E001–E003 was wrong.** British Columbia has kept UTC-7 all year since 2026-03-08, so there was no fall-back on 2026-11-01. `transit_env` had tzdata 2025b, which still fell back to UTC-8, so every local time from Nov 1 came out one hour early. Transit BC's labels and event times were correct throughout.
- Diagnostic before the change (dev window, not a scored run): the first and last vehicle of each day, direction 0, Tue–Thu. Oct 13–29: first seen about 05:30 with label 05:33, last about 00:30 with label 23:53. Nov 3–5, as converted then: first about **04:33** with label **05:33**, last about **23:33** with label 23:53. The same labelled trips appear one hour earlier, which a relabelling can't produce (the 05:33 label would vanish). In UTC, the first bus leaves at about 12:30 in both months.
  - System tzdata 2026e has the change (`zdump`: "MST", still UTC-7, from 2026-11-01). The [BC government release](https://archive.news.gov.bc.ca/releases/news_releases_2024-2028/2026AG0013-000209.htm) confirms it.
  - The stored data needs no fix. Ingestion stores feed epochs as UTC and the start descriptor as local text; feed header, event times and Pi read times agree in every month.
  - This also explains the E000/E001 "morning peak shift" and why the timetable clock beat the feed clock on Nov 2–8: the timetable clock adds the measured offset back, which cancels the conversion error.
  - E010 attributed some 1 h stale-report errors to the #13 offset. That attribution no longer holds; those comparisons are all in UTC.
- Change: `transit_env` tzdata updated to 2026e (conda-forge) and the pip `tzdata` to 2026.6 (environment only). [1d996ac](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/1d996ac): `feed_clock.py` docstring corrected, a guard test fails if the tz database is stale, and DST unit tests moved from the non-existent 2026-11-01 fall-back to the real 2025-11-02 one. No harness or config change.
- Prediction before running: identical scores and rows to E010, because the timetable clock is unchanged (feed clock +1 h, offset −60 min). The November offset table flips: +60 → 0, and Nov 1–2 schedule-only journeys 0 → −60.
- Git: [1d996ac](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/1d996ac) (clean)   MLflow run: [f458246392df4df39e7259c10dd71fc5](http://127.0.0.1:5000/#/experiments/1/runs/f458246392df4df39e7259c10dd71fc5) (an identical `--tracking files` copy is in `data/experiments/hwy97_delay/20261122T050756Z_harmonic_huber_dev/`)
- Results (dev, the same **27,742** visits as E010):
  - **Delay MAE:** B0 166.3 s, B3 136.4, **harmonic 134.6**, shuffled control 240.1. Every fold matches E010 exactly (harmonic / B3: dev1 152.1 / 159.7 … dev6 119.4 / 117.8).
  - Harmonic vs B0: −31.7 s [−38.8, −25.0], −19.1%. **−1.8 s vs B3.**
  - **Offsets by month:** Sep 98.9% at 0, Oct 99.9% at 0, **Nov 92.7% at 0** and 7.3% at −60 (E003: 92.7% at +60, 7.3% at 0). The −60 journeys are Nov 1 and early Nov 2, where Transit BC's own outage-day data applied the old fall-back; all of Nov 1 is already excluded as `untracked_journey`.
- Sanity checks: leakage assertions passed; identical rows; shuffled control passed; 617 forecasting and 18 ingestion unit tests pass.
- Verdict: **keep.** E010 stays the reference baseline. Keep `calendar_clock: "timetable"`: it is robust to a wrong local conversion and now equals the feed clock for almost every journey.
- Next: unchanged (H8 #25, H9).

### E013: anchor-free secondary score, baseline rescored

- Purpose: owner decision (2026-11-22). Add the anchor-free score as a secondary metric (RESEARCH.md Metrics) and rescore the reference baseline, so H8 has an anchor-free comparison point.
- Change: [b0a8101](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/b0a8101). Evaluation-only `actual_departure_delay_s` and `chain_anchor_delay_s` columns; `anchor_free_scores` in `score_predictions`; MLflow `anchor_free_*` metrics; printed table; anchor-free MAE in the LOG row. Two unit tests: a chain that is wrong only by its anchor scores 0, and an end-to-end run.
- Prediction before running: primary scores identical to E010/E012 (harmonic 134.6 s, B3 136.4 s, same 27,742 rows). Anchor-free (from the E011 exploration, which included holiday rows): harmonic 116–119 s, B3 120–123 s, harmonic vs B3 −3 to −5 s; harmonic bias about −15 s, B3 about 0 s; fewer than 1% of rows without an actual departure.
- Git: [bb12d9b](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/bb12d9b) (clean)   MLflow run: [eaec6ba932224a028e518e2fc0810ac7](http://127.0.0.1:5000/#/experiments/1/runs/eaec6ba932224a028e518e2fc0810ac7)
- Results (dev, the same 27,742 visits):
  - **Primary, as predicted:** identical to E010/E012. B0 166.3 s, B3 136.4, harmonic 134.6; harmonic vs B0 −31.7 s [−38.8, −25.0].
  - **Anchor-free, as computed in this run:** harmonic 123.0 s (bias −25.9), B3 126.3 (−10.8), B1 142.8, B2 231.9; harmonic vs B3 −3.3 s [−7.7, +0.4]; 0 rows without a departure.
- Sanity checks: leakage assertions passed; identical rows; shuffled control passed (240.1 vs B2 240.8).
- Interpretation: **prediction failed for the anchor-free score** (expected harmonic 116–119 s). Tracing the gap to the exploration's 117.7 s found a defect in the new metric. Four journeys on 2026-11-02 (outage restart) have two stop-1 departure times at the same publication, 1 h apart, both reporting 0 s delay. The run took the actual departure delay from time arithmetic (−3,600 s) rather than the reported `departure_delay` that truth uses. Those 52 rows added 6.7 s to the anchor-free MAE. The primary score is unaffected.
- Verdict: **superseded by E014** (anchor-free departure delay taken from the reported field, like truth). Primary results stand.


### E014: anchor-free baseline rescore, departure from the reported delay

- Purpose: rerun E013 after fixing the anchor-free departure definition: the final reported `departure_delay` at the first stop (consistent with truth, which uses the reported `arrival_delay`); time arithmetic only as a fallback. Unit test for the two-clock case.
- Prediction before running: primary identical to E013. Anchor-free: the 52 affected rows lose their ~3,600 s errors, so harmonic about 123.0 − 6.7 ≈ 116 s and B3 about 119–120 s; harmonic vs B3 about −3 s; harmonic bias about −15 to −20 s.
- Git: <pending>   MLflow run: <pending>

<!-- Template for new entries:

### E0NN: <short title>

- Hypothesis: H?
- Prediction before running: <expected direction and size>
- Change: <what code/config changed>
- Git: [<hash>](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/<hash>)   MLflow run: [<run_id>](http://127.0.0.1:5000/#/experiments/1/runs/<run_id>)
- Results (dev folds): MAE per method; Δ vs B0 with CI; Δ vs best baseline; per-fold spread
- Sanity checks: leakage assertions passed? shuffled control? identical row counts?
- Verdict: keep / discard / refine / inconclusive
- Next: <what this suggests>
-->
