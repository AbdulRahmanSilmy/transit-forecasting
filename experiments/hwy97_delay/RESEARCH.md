# Research charter: recurring traversal + delay dynamics vs Transit BC's earliest estimate

This file steers the research loop. The human owner edits **Question, Success criterion, Locked test, Open decisions and Backlog priority**. Agents may append to the Backlog and Findings sections, but must not change the definitions below without asking.

## Question

For Route `97-KEL`, direction 0 (Highway 97), at the **earliest moment Transit BC publishes a delay estimate for a journey** (about 150 minutes before arrival), does a forecast built from

1. learned **recurring traversal times** for each directed stop-to-stop segment, chained through
2. the **delay dynamics identity** $d_k = d_{k-1} + T_k + W_k - S_k$,

predict final arrival delay at each stop more accurately than Transit BC's own earliest estimate?

Scope: repeated patterns only. Anomalous delays (incidents, detours) stay in the scores but are not modelled. The **first-stop departure delay is out of scope** too (owner, 2026-11-21): the anchor is always Transit BC's earliest estimate. The owner treats departure delay, like anomalous traversal increases, as the stochastic part of delay, to be handled later by a more real-time method.

## Definitions (frozen; change only with owner approval)

| Item | Definition |
|---|---|
| Prediction unit | One stop visit `(trip_instance_key, stop_sequence)` on a direction-0 journey |
| Origin `t0` | The journey's **first** trip-update publication. `lead = final arrival_time - t0` is recorded for every row |
| Truth | Final reported `arrival_delay` (latest publication). This is a feed proxy, not an independent measurement. If that report was published **more than 10 min before the arrival it reports**, it is an un-updated prediction: the visit is excluded as `stale_final_report` (≈3% of visits; also applied to both ends of training traversals, **except a first-stop departure**: the feed leaves on-time first-stop departures at their 0 s origin estimate, and vehicle positions confirm 91% are within 60 s, so those links train (owner, 2026-11-21, E010)). Approved by owner 2026-11-19 as an interim rule; a position-based truth is tracked in [issue #12](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/12). A journey whose final reported arrival and departure delays are **all exactly 0 s** is untracked (schedule-only estimates, e.g. all of 2026-11-01): all its visits and training links are excluded as `untracked_journey` (owner, 2026-11-21, [issue #21](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/21)) |
| Comparator B0 (Transit BC) | `arrival_delay` for that stop in the latest publication at or before `t0` |
| Scheduled allowance `S_k` | From origin-time feed fields only: `(A - D)` at `t0` for both endpoints. Never the latest publication |
| Traversal `T_k` | Upstream departure to downstream arrival (excludes upstream dwell), as in the harmonic notebook |
| Dwell `W_k` | **Not modelled.** Fixed rule (see Open decisions); default is the per-stop training median |
| Anchor `d_0` | Transit BC's earliest estimate at the journey's **first** stop, for every method. All accumulated change after the anchor comes from our model. The first-stop departure delay is not learned in this study (owner, 2026-11-21; see Scope). The harness's opt-in `--anchor` option (added for H6, E009) is not used for study runs |
| Candidate forecast | $\hat d_k = d_0 + \sum_{j \le k} (\hat T_j + \hat W_j - S_j)$ |

Anchoring on Transit BC's first-stop estimate is deliberate. Both methods start from the same information, so any difference measures only whether our traversal dynamics propagate delay better.

## Metrics

- **Primary:** MAE of arrival delay (seconds) over all scored stop visits, and the **paired difference vs B0** with a 95% bootstrap CI that resamples **service days** (rows on the same day are correlated).
- **Secondary:** MAE by stop (error accumulates downstream), by lead bin, by peak/off-peak and weekday; bias; RMSE; paired win rate; macro-stop MAE.
- **Anchor-free (secondary, owner 2026-11-22):** delay MAE, bias, by fold and by stop for the chained methods (B1, B2, B3, candidate, control) after removing each journey's anchor error (chain anchor minus the actual first-stop departure delay), plus each method's paired difference vs B3 with the same day-bootstrap CI. It measures only the delay accumulated after departure, which is the study's scope; it decides keep/discard for propagation hypotheses. B0 is not chained and is excluded. Rows without an actual departure are counted and dropped from this score only.
- **Diagnostic only:** a "typical" stratum (B0 absolute error below its training P90). This is never used for tuning or claims.
- **Excluded from primary scoring:** BC statutory holidays (e.g. Thanksgiving Oct 12, Remembrance Day Nov 11). They are scored and reported separately.

## Baseline ladder (every run reports all of these on identical rows)

| ID | Forecast | Why it matters |
|---|---|---|
| B0 | Transit BC earliest estimate | The target to beat |
| B1 | Anchor held constant ($\hat T + \hat W = S$) | Is B0 just schedule propagation? |
| B2 | Identity with per-segment training **median** traversal | Dynamics without any calendar pattern |
| B3 | Identity with segment × weekday × hour median traversal | Simple calendar lookup |
| C* | Identity with a learned traversal model (harmonic, pooled, ...) | The hypothesis |

A candidate supports the hypothesis only if it beats **B0 and B3**. Beating B0 alone could be credited to the dynamics identity rather than learned recurring patterns. (Prior evidence: on segment 4, the harmonic model lost to a constant median.)

## Success criterion (predeclared)

On the locked test set, the candidate's paired MAE difference vs B0 has a day-clustered 95% CI entirely below zero. Its relative MAE reduction must be **≥ 5%**, and it must also beat B3. The owner may revise the 5% threshold before the test is unlocked, never after.

The anchor-free score (see Metrics) is **secondary**: it never replaces this criterion, and the headline comparison with Transit BC stays as scored.

## Data and splits

- Source: Cloudflare R2 archive. Trip updates are available from **2026-01-11** (audit coverage first).
- **Development:** service days up to and including 2026-11-09. Use expanding-window weekly CV folds over the most recent 6 weeks (2026-09-28 to 2026-11-08), with a 1-day embargo. Data before the first fold is training-only.
- **Embargo:** 2026-11-10.
- **Locked test:** service days from **2026-11-11** through **2026-11-24** (at least two full weeks). Agents never load, score or plot this period. It is unlocked **once**, by the owner, for the final candidate.
- Service day = local date with a 03:00 cutover, `America/Vancouver`.

## Leakage guardrails (the harness asserts these)

1. No feature for a row uses any publication after that journey's `t0`.
2. `S_k` and the anchor come from origin-time fields only.
3. Traversal/dwell training uses only visits whose **final** publication is before the fold cutoff.
4. All methods are scored on exactly the same rows. Rows any method cannot predict are counted and reported, never silently dropped.
5. Shuffled-target control: a model trained on permuted traversals must not beat B2.

## Decisions (owner, 2026-11-19)

- [x] Dwell rule: **per-stop training median**. Zero dwell may run only as a labelled ablation (H5).
- [x] Origin: **earliest publication per journey**.
- [x] Scope: **direction 0 only**. Direction 1 is out of scope for this study.
- [x] Minimum effect size: **5% relative MAE reduction** vs B0 (revisable before the test is unlocked).
- [x] Stale final reports (> 10 min before the reported arrival) are excluded from truth (2026-11-19, interim; see issue #12).
- [x] Stale first-stop departures still train the first link (2026-11-21, E010): the stale rule misread on-time departures as old predictions. At stops 2–14 the rule stays: 0 of 53 stale reports with vehicle pings were within 60 s.
- [x] Untracked journeys (every final delay exactly 0 s, e.g. all of 2026-11-01) are excluded from scoring and training as `untracked_journey` (2026-11-21, #21).
- [x] Anchor: **Transit BC's earliest estimate only** (2026-11-21, after E009). First-stop departure delay is treated as stochastic and left to a later real-time method, so H6 and any refined anchor are out of scope.
- [x] Anchor-free secondary metric (2026-11-22, before H8's scored run): chained methods are also scored as if started from the journey's actual first-stop departure (evaluation-only; never a feature). It decides keep/discard for hypotheses about delay propagation (e.g. H7, H8). The success criterion and the comparison with Transit BC stay as scored. B0 is excluded; B1 becomes "delay stays as at the actual departure".
- [x] Clock handling (2026-11-19, owner-directed fix after investigation, [issue #13](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/13)):
  - Cause (corrected 2026-11-22, E012): BC has kept UTC-7 since 2026-03-08, but `transit_env`'s timezone database still fell back to UTC-8 on 2026-11-01, so local times from Nov 1 looked **1 h early**. The feed's labels and times were correct. Keep tzdata current; a unit test fails if it is stale.
  - Event times are physical: the header clock matches real time, and vehicle GPS is at the stop at the reported arrival time. So they are **never shifted**, and final reports remain valid truth.
  - Service days and calendar features use the **timetable clock** (feed schedule plus the journey's whole-hour offset to its start descriptor; `calendar_clock` in config.json), because recurring behaviour is keyed to the trip. It also stays right if a local-time conversion is wrong; with a current timezone database it equals the feed clock for almost every journey.
  - Journeys with non-whole-hour offsets are excluded (`schedule_clock_mismatch`).

## Backlog (agent proposes, owner prioritises)

**Priority lives on the GitHub project [Hwy 97 delay forecasting](https://github.com/users/AbdulRahmanSilmy/projects/3)** (`Priority` field, P1 first, then board order), not in this table. Rows here are in ID order. Open items are tracked as GitHub issues: H6 #14, H7 #15, H2 #16, H4 #17, H3 #18, H5 #19, H8 #25, H10 #43. Locked test: #20. Data-truth work: #12 (position-based arrivals), #21 (untracked journeys).

| ID | Hypothesis | Cheapest test | Status |
|---|---|---|---|
| H0 | Build the harness and baseline ladder (B0 to B3) | Iteration 0: implement and reproduce B0 | done (E001) |
| H1 | **Priority.** The morning traffic peak shifted after Nov 1, coinciding with the DST change (Sun **2026-11-01**), and this caused the harmonic model's worse holdout performance. Either a timezone bug (1-hour **phase** shift in features or clocks) or a genuine change in timing/level | Compare fitted vs observed morning-peak timing and height by week around Nov 1; audit every tz conversion | **confirmed (E002):** a timestamp defect (issue #13), not traffic. After correction, dev6 harmonic MAE 144→119 s |
| H2 | Longer history (vs two months) gives more stable recurring patterns than the 2-month window. **Archive check (E003 notes):** Feb–May 2026 is empty and route-tagged data starts around June/July, so this means about 4–5 months, not 10 | Same model, training start ~July vs Sep | open |
| H3 | A recent-level term (e.g. trailing 2-week median offset) fixes the level shift without chasing anomalies | Harmonic + rolling level vs harmonic | **refuted at 2-month history (E007):** 138.5 s vs harmonic 134.5; best traversal MAE (33.4 s) but more negative bias. Untested with longer history (H2) |
| H4 | A pooled model across segments (segment effects + shared calendar) beats per-segment models | Pooled vs per-segment harmonic | **refuted (E005, E006):** shared shape 165.1 s, partial pooling 146.4 s vs harmonic 134.5 and B3 137.0 |
| H5 | Dwell median vs zero changes the conclusion | Ablation | open |
| H7 | Chained delay error grows along the route, so by stops 13–14 our methods only match B0 despite traversal errors half of B0's (E004). Buses ahead of schedule hold at timepoints, so delay recovers toward the timetable. A recovery-aware chain (e.g. early arrivals clipped to the schedule at timepoints, or learned per-segment recovery) beats the plain sum | Clip predicted delay at timepoint stops to ≥ 0 (or learn a recovery fraction) and compare delay MAE by stop with E004 | open (proposed by agent); **E011 evidence:** holding at stops 6 and 10 accounts for −50 s of the end-of-route bias |
| H6 | Departure delay at the first stop has a recurring pattern. All methods are biased early (−53 to −138 s, E001) because the anchor is Transit BC's 0-delay origin estimate | Learn a calendar median of first-stop departure delay as the anchor | **out of scope (owner, 2026-11-21).** E009: a calendar-median anchor removed the bias (harmonic −71 → +3 s) but MAE was 136.3 vs 134.6 s, partly because training only on fresh reports skews it ~40 s late. The study keeps the feed anchor; first-stop departure delay goes to a later real-time method |
| H8 | Chained delay sums traversals, so it needs **mean**-unbiased traversal forecasts. Median-type and log-space models under-predict sums of right-skewed traversals (E006, E007: better traversal MAE but more negative bias, worse delay MAE). A per-segment × hour retransformation correction (training mean of actual / predicted) lowers delay MAE | Harmonic × per-segment-hour smearing factor from training residuals vs harmonic; check traversal bias by segment-hour, not just MAE. No harness change needed | **kept (E017).** E015's uncentred correction failed the shuffled control, because its segment-level term passes as skill; the owner chose the centred version (option (c)). E017 vs harmonic: scored −1.7 s [−2.9, −0.4], anchor-free −3.2 s [−4.5, −1.7], from the 16–17 h peak and overnight; the evening gets worse. Explored in E011 and E017 ([#25](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/25)) |
| H9 | The chained forecast's early bias grows along the route (harmonic −44 s at stop 2 to about −100 s at stop 14; E009) because the per-stop **median** dwell rule under-predicts mean dwell (bias −4.0 s × 12 stops) on top of traversal bias (−1.3 s × 13 links). A **mean** dwell rule (per-stop training mean, or median × smearing factor) removes most of that growth and lowers delay MAE | Dwell rule ablation alongside H5: per-stop mean vs median, harmonic traversal, feed anchor. **Needs a harness change** (the dwell rule is frozen in `evaluate_block`) | **superseded by H7 (E011):** the dwell bias is timepoint holding at stops 6 and 10 (dwell ≈ constant − arrival delay for early buses), so a constant mean dwell is the wrong model |
| H10 | Harmonic's smooth daily curve misses sharp peaks: E017's centred hour correction gains 26 s at 16 h and 19 s at 17 h, but its per-cell estimates add noise at other hours (evening +5 to +18 s). A base model with a sharper per-segment daily shape (more Fourier terms, or an hour-of-day term regularised towards the harmonic curve) captures the peak directly, without a separate correction | Sharper harmonic vs E017 on the same folds; judge on anchor-free delay MAE by hour, not just overall | **not supported (E018, E019; [#43](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/43)).** Order 12 (E018) moves little: anchor-free +2.1 s [+0.7, +3.4] vs E017. A penalised per-segment × hour effect in harmonic's log Huber loss (E019) beats E017 on the scored metric (130.4 s; −0.7 [−1.3, −0.1]) and removes most of its evening loss, but is worse anchor-free (113.1 s; +0.8 [+0.05, +1.5]). The gap it misses is skew (mean above the typical trip) overnight and at 16–17 h |
| H11 | E019 fixes the typical-trip hour shape; what's left is skew, which E017's mean correction captures but distorts the evening by centring on a skew-inflated segment mean. E017's centred mean correction applied **on top of E019** keeps E019's evening and recovers E017's overnight and peak gains | `ResidualHourCorrection(HourEffectTraversal(harmonic), centred=True)` vs E017 and E019 on the same folds; anchor-free MAE by hour. No harness change needed | open (proposed by agent, E019) |

## Findings

Record concluded results here, one bullet each, with the LOG entry IDs behind them.

- **Prior evidence, segment 4 only (E000).** During training, the harmonic traversal model tracked recurring traffic better than Transit BC's earliest estimate, especially the **early-day (morning) peak, which Transit BC under-predicts**. Caveat: this is an in-sample fit, not held-out skill.
- **Prior evidence, segment 4 only (E000).** In the holdout week (Nov 3–9, after Nov 1), the **morning peak appears shifted** relative to the learned pattern. The harmonic model then did worse than Transit BC's earliest estimate (traversal MAE 1.248 vs 1.022 min) and a constant median (1.133 min). The cause is unresolved; see H1.
- Implication: the recurring pattern Transit BC misses is real enough to motivate the study. Whether it survives a regime change like Nov 1 is the main risk.
- **The Nov 1 "peak shift" was a data defect (E002).** Realtime timestamps written since the 2026-11-02 ingestion restart are 1 h early (issue #13). With the temporary correction, calendar models beat Transit BC in every dev fold, including Nov 2–8. Route-wide dev result: harmonic −19.1% MAE vs B0. But it's only 2.6 s better than a calendar median (B3).
- **Pooling across segments hurts on this route (E005, E006; H4 refuted).** Every segment has more than 1,200 dev traversals, so there's no low-support segment to rescue, and a shared or shrunk calendar shape flattens the peaks of seq 6, 10 and 11. **Chaining turns small per-link biases into large delay errors:** E006 beats B3 on traversal MAE (35.0 vs 35.5 s) but loses on delay MAE (146.4 vs 137.0 s), because a −3 s peak-hour bias per link adds up over 13 links. Judge traversal candidates on per-segment × hour **bias**, not just traversal MAE.
- **A recent-level offset doesn't help at 2-month history (E007; H3 refuted for now).** It gives the best traversal MAE so far (33.4 s) but loses 4 s of delay MAE to harmonic in every fold, because the median-based offset lowers predictions of right-skewed traversals. With E006, this is the second time better traversal MAE has come with worse delay MAE. **Delay is a sum, so traversal candidates should be mean-unbiased** (proposed H8).
- **A learned first-stop anchor removes the bias but not the error (E009; H6 refuted in this form).** A calendar median of fresh first-stop departures shifts every forecast later by about 73 s, turning harmonic's delay bias from −71 to +3 s, but MAE gets 1.7 s worse. The anchor runs about 30 s late because on-time departures tend to leave stale reports, which are excluded from training. Most of the early bias **grows along the chain** (−44 s at stop 2 to −100 s at stop 14), consistent with mean-biased per-link dwell and traversal rules, so it needs per-link fixes (H8, proposed H9) rather than one offset at the origin.
- **Correction to the E002 finding (E012): the Nov 1 shift was our timezone database, not the feed.** BC has kept UTC-7 since 2026-03-08, but `transit_env`'s tzdata still fell back to UTC-8 on 2026-11-01, so local times from Nov 1 looked 1 h early. Transit BC's labels and times were correct. The timetable clock already cancelled the error, so no scores change (E011 = E010).
- **Stale reports are wrong except at the first stop (E010, vehicle positions).** At stops 2–14, none of the 53 stale reports with vehicle pings was within 60 s of the bus. But 91% of stale first-stop departures (0 s) were, because the feed leaves on-time departures at their origin estimate. Keeping them adds 1,297 first-link training traversals and improves that link's forecasts, but chained delay MAE doesn't change (harmonic 134.6 s, B3 136.4 s; the new reference baseline).
- **Overnight service was missing until E016 (#36).** Trips starting at or after 24:00 had no start descriptor, so E001–E015 never trained or scored 00:00–03:00 journeys. Recovering them adds 1,550 scored visits with small delays (B0 101 s), so headline MAEs fall (harmonic 132.9 s, B3 134.2, B0 162.8; the new reference baseline). But on the original rows, harmonic and B3 get 1.1 and 0.7 s worse: rules not keyed to time of day (per-stop dwell medians, the daily harmonic shape) absorb the overnight regime. Harmonic vs B3 narrows to −1.3 s.
- **A centred per-segment × hour correction improves harmonic (E017; H8 kept).** Removing E015's segment-level term fixes the shuffled-control failure. The gain (scored −1.7 s and anchor-free −3.2 s vs harmonic; anchor-free −6.1 s [−10.0, −2.6] vs B3) comes from the sharp 16–17 h peak and the overnight trips, which harmonic's smooth daily curve under-fits. Per link it does nothing (traversal MAE +0.2 s): it pays off only because same-hour corrections add up along the route. Evening hours get worse.
- **A sharper typical-trip shape is not enough (E018, E019; H10 not supported).** The 16–17 h peak is a weekday effect about 1 h wide. Daily order 12 (shortest wave 2 h) can't follow it, even in sample (E018: anchor-free 114.4 s, worse than E017's 112.3). A penalised per-segment × hour effect in harmonic's own log Huber loss halves the peak's median gap and doesn't touch other hours. It gives the best scored MAE so far (E019: 130.4 s; vs B3 −3.8 [−7.5, −0.4]), but it is +0.8 s worse anchor-free than E017. Half the peak gap, and most of the overnight gap, is **skew**: the mean sits above the typical trip, and a median-type fit can't capture that. E017's mean correction does, but centring on a skew-inflated segment mean pushes the evening down.
