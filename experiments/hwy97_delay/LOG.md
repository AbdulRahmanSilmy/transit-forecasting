# Experiment log: Hwy 97 delay forecasting

Append-only. One entry per run, including failed and negative results. Each entry must be reproducible from its git hash, and cites its **MLflow run ID** (local store `data/mlflow/`, experiment `hwy97_delay`; see HARNESS.md). MLflow holds the numbers, figures and predictions. This log holds the reasoning.

Scores are **development folds only** unless the entry is marked `LOCKED TEST` (owner-approved, at most once per candidate).

## Summary table

| ID | Date | Git | Hypothesis | Change | MAE (s) | Δ vs B0 [95% CI] | Δ vs best baseline | Verdict |
|---|---|---|---|---|---|---|---|---|
| E000 | 2026-11-19 | 56c993e | prior evidence | Segment 4 notebook, not run under the harness | see below | n/a | n/a | context |
| E001 | 2026-11-20 | bb769cf | H0 | dev: harmonic_huber (7741d709320c49ae9da47ce907853ecd) | 139.0 (B0 166.2) | -27.2 [-36.0, -18.6] (-16.4%) | -4.2 vs B3_calendar_median | provisional baseline |
| E002 | 2026-11-20 | 64f51eb | H1 | dev: harmonic_huber (f38bff5b31864f3497745333592f9ee2) | 134.4 (B0 166.1) | -31.7 [-38.8, -24.7] (-19.1%) | -2.6 vs B3_calendar_median | H1 confirmed; superseded by E003 |
| E003 | 2026-11-20 | 5d9cfa5 | H1 | dev: harmonic_huber (98ca4b712d70439eaf1fde2a555d32fe) | 134.5 (B0 166.2) | -31.7 [-38.8, -24.7] (-19.1%) | -2.5 vs B3_calendar_median | keep; current baseline |

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
- Git: bb769cf   MLflow run: 7741d709320c49ae9da47ce907853ecd
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
- Git: 64f51eb   MLflow run: f38bff5b31864f3497745333592f9ee2
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
- Git: 5d9cfa5   MLflow run: 98ca4b712d70439eaf1fde2a555d32fe
- Results (dev, 27,759 visits, the same rows as E001):
  - MAE: B0 166.2 s, B2 240.5, B3 137.0, **harmonic 134.5**.
  - Harmonic vs B0: −31.7 s [−38.8, −24.7], −19.1%, win rate 0.63.
  - dev6: harmonic 119.6, B3 118.2, B0 141.7.
- Sanity checks: monthly offsets are Sep 98.9% / Oct 99.9% at 0, and Nov 92.7% at +60, 7.3% at 0 (Nov 1 and early Nov 2). Leakage assertions passed; shuffled control passed.
- Verdict: **keep. This is the current baseline.** It confirms the clock interpretation. Harmonic vs B3 is still only −2.5 s overall, and harmonic loses to B3 in dev4 and dev6.
- Next: H6 (first-stop departure delay; bias −55 to −140 s), and H2 with the realistic history (about June/July onwards).

<!-- Template for new entries:

### E0NN: <short title>

- Hypothesis: H?
- Prediction before running: <expected direction and size>
- Change: <what code/config changed>
- Git: <hash>   MLflow run: <run_id>
- Results (dev folds): MAE per method; Δ vs B0 with CI; Δ vs best baseline; per-fold spread
- Sanity checks: leakage assertions passed? shuffled control? identical row counts?
- Verdict: keep / discard / refine / inconclusive
- Next: <what this suggests>
-->
