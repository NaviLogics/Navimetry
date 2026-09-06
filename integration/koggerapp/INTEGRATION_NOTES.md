# Unified Navimetry integration notes

## Phase 0 — current skeleton

Goal: establish the integration boundary without destabilizing either codebase.

Invariants:

1. Keep `navimetry-0.2` as the reference implementation of bathymetry processing and Survey-aware QC.
2. Do not merge the unified work into `main` until the KoggerApp-derived build is reproducible.
3. Preserve the original KoggerApp GPLv3 license and upstream copyright/attribution notices.
4. Rebrand the product UI and executable as Navimetry, but do not rewrite upstream internals merely for naming consistency.
5. The first Bathymetry page is UI-only. No processing mathematics is duplicated yet.

## Phase 1 — real KoggerApp-derived branch

Create a writable NaviLogics fork of `koggertech/KoggerApp`, then apply `apply_skeleton.py` to the clean upstream checkout. The fork should track upstream master separately from the Navimetry integration branch.

Recommended branches in that fork:

- `upstream-master` — mirror of KoggerApp master;
- `navimetry-unified` — Navimetry product branch;
- short-lived feature branches for acquisition, bathymetry UI, engine bridge, and branding.

## Phase 2 — UI wiring

Wire a Bathymetry navigation action into the existing KoggerApp navigation/menu system and load `NavimetryProcessingPage.qml` inside the same application shell. Keep sonar setup, echogram, maps, KLF opening and live connections unchanged.

## Phase 3 — engine bridge

Before any C++ rewrite, expose the stable Python Navimetry processing engine through a narrow contract:

- input project/KLF/CSV paths;
- JSON processing configuration;
- progress events;
- JSON result/report;
- generated artifacts directory.

Run it initially as an external process from Qt. This keeps the mathematical reference implementation isolated and testable.

## Phase 4 — native integration

After field calibration and golden-dataset validation, migrate only stable modules to C++/Qt where there is a clear benefit. Every migrated module must be numerically compared with the Python reference on the same golden datasets.

## Initial product navigation target

- Survey
- Sonar
- Map
- Echogram
- Bathymetry
- QC & Reports
- Settings

Bathymetry subflow:

- Data
- Surface
- Export

QC & Reports subflow:

- Observations
- Survey Lines
- Triangle QC
- Coverage
- Processing Report

## Branding

The final asset replacement should cover application icon, window title, splash/about screen, README imagery, installer/package metadata and mobile launcher icons. A final Navimetry logo asset has deliberately not been invented in this skeleton; use the approved project artwork when supplied.
