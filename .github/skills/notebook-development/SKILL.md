---
name: notebook-development
description: "Use when creating, editing, or refactoring Jupyter notebooks (.ipynb) in this repository. Keep notebook code simple, human-readable, and focused on analysis; encapsulate complex or reusable logic in tested forecasting functions or classes."
---

# Readable Notebook Development

## Goal

A notebook should read like an analysis, not an application implementation.
Keep the question, configuration, main steps, tables, and plots visible.
Hide implementation details behind well-named package APIs, not behind large
helper functions defined inside the notebook.

## Procedure

1. Read the relevant cells and their dependencies. Preserve user-selected
   parameters, dataset IDs, cell order, and unrelated work.
2. Look for an existing package utility before adding logic. Keep reusable
   forecasting behavior in `forecasting/forecasting/` and expose intended
   public functions or result classes from its `__init__.py`.
3. Keep cells short and purpose-driven: readable configuration, a small number
   of descriptive function calls, and inspection or visualization of results.
   Keep simple one-off analysis and plotting in the notebook when that is
   clearer than introducing another abstraction.
4. Move branching workflows, credential handling, downloads, persistence,
   validation, substantial transformations, and repeated plotting logic into
   package functions. Use a class only when related state or named results
   make the interface clearer. Prefer the simplest useful API.
5. Give users explicit, readable controls for behavior such as cache versus
   download. Avoid hidden state, implicit network requests, silent fallbacks,
   and replacement of a requested historical dataset with fresh data.
6. Document package functions with NumPy-style docstrings. Test their normal,
   boundary, missing-data, and failure paths with pytest. Test package behavior
   directly rather than coupling tests to exact notebook source strings.
7. Update nearby Markdown to explain the analysis, controls, assumptions, and
   result interpretation. Leave implementation mechanics in package docs.
8. Validate the changed cells with their required setup in `transit_env`,
   using existing local snapshots or mocked external services when possible.
   Validate notebook syntax/schema and run focused package tests. Preserve
   unrelated outputs and do not rerun expensive or network-dependent cells
   unnecessarily.

## Readability Check

- Can a reader understand the workflow without tracing nested branches?
- Are configuration and units obvious?
- Does each package call describe an analytical step?
- Are complex details tested outside the notebook?
- Were unrelated cells left alone?
