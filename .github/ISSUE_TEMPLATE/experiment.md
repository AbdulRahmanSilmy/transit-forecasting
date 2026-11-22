---
name: Experiment
about: A hypothesis to test with a study's evaluation harness
title: "H?: "
labels: experiment
---

Study: `experiments/<study>` (backlog **H?**, proposed by <owner / agent in E???>).
<!-- Add the matching `study:<study>` label, put the ticket on the study's project board and add the row to RESEARCH.md's Backlog. -->

## Hypothesis
<!-- A specific, falsifiable claim, and the evidence that motivates it (cite LOG entry IDs). -->

## Test
<!-- The cheapest run that could refute it: the candidate vs which model, on dev folds. -->
<!-- State whether it needs a harness change. If it does, it needs the owner's explicit approval before any work starts. -->

## Success
<!-- Decided before running: which metric must move, by how much, against which baselines (B0 and the best baseline). -->
<!-- Also name the diagnostics that must not get worse, such as bias. -->

## Experiment runs
<!-- One line per LOG.md entry; the research loop updates this list. MLflow links need the local UI running (see LOG.md). -->
