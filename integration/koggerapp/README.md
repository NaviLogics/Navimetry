# Navimetry unified integration skeleton

This directory is the first integration skeleton for the future unified **Navimetry** application based on KoggerApp.

## Upstream baseline

- Upstream: `koggertech/KoggerApp`
- Baseline branch: `master`
- Baseline commit reviewed for this skeleton: `4615cc88dcb865134d973bf132034ff6f41dbb57`
- Upstream application architecture: C++23 + Qt 6.8/QML + CMake.

## Target architecture

KoggerApp becomes the acquisition / device / visualization shell. The existing Navimetry Python implementation remains the reference bathymetric-processing and QC engine until the release mathematics is frozen and ported or embedded deliberately.

Initial UI target:

- existing KoggerApp functionality remains intact;
- application branding becomes **Navimetry**;
- a new top-level bathymetry entry opens **Navimetry Processing**;
- the processing page is intentionally a placeholder in this skeleton and does not call the Python engine yet.

## Files in this overlay

- `qml/NavimetryProcessingPage.qml` — empty Bathymetry / Navimetry Processing page.
- `qml/NavimetryBathymetryButton.qml` — simple navigation button suitable for insertion into the existing KoggerApp navigation area.
- `apply_skeleton.py` — idempotent patch helper for a clean KoggerApp checkout. It performs text rebranding in the main CMake/QML entry points and installs the placeholder QML files.
- `INTEGRATION_NOTES.md` — exact next integration steps and invariants.

## Important

This branch intentionally does **not** replace the current `navimetry-0.2` processing implementation and does not merge into `main`. It is an integration overlay until a writable NaviLogics fork of KoggerApp exists in GitHub. Once such a fork exists, this overlay should be applied there and the resulting KoggerApp-derived source tree should become the real unified branch.

The KoggerApp GPLv3 license and upstream attribution must remain in the unified application. Vendor permission for branding/use is complementary to, not a replacement for, the source license obligations.
