# Navimetry × KoggerApp integration skeleton

This branch is the first non-destructive integration milestone for the future unified **Navimetry** application.

## Pinned upstream

- KoggerApp repository: `https://github.com/koggertech/KoggerApp`
- upstream branch: `master`
- pinned commit: `4615cc88dcb865134d973bf132034ff6f41dbb57`
- implementation: C++23 + Qt 6.8/QML + CMake.

The branch keeps the existing Navimetry Python implementation untouched as the reference bathymetry/QC engine and mounts KoggerApp as `upstream/KoggerApp` via a Git submodule. This avoids copying or rewriting upstream history while field calibration of Survey-aware QC continues.

## What the skeleton does

`apply_skeleton.py` applies an intentionally small, auditable overlay to the pinned KoggerApp checkout:

1. visible application brand becomes **Navimetry**;
2. generated desktop executable is named `Navimetry` while the internal CMake target remains `KoggerApp` to minimize first-stage churn;
3. a desktop menu **Bathymetry** is added;
4. **Bathymetry → Navimetry Processing** opens a placeholder page;
5. no Navimetry processing algorithm is connected yet;
6. KoggerApp acquisition, device control, echogram, map and visualization code are otherwise untouched.

## Try the skeleton locally

```bash
git checkout navimetry-unified-koggerapp
git submodule update --init --recursive
python integration/koggerapp/apply_skeleton.py
```

Then open `upstream/KoggerApp/CMakeLists.txt` in Qt Creator and build with the normal KoggerApp toolchain. The overlay edits the submodule working tree only; rerun after resetting the submodule if you want a clean reproduction.

## Branding asset

This milestone changes the textual/application brand and prepares the unified UI entry point. A final Navimetry logo/icon has **not** been fabricated here. When the approved Navimetry logo asset is supplied, replace the KoggerApp image/icon resources in a separate branding commit so visual branding remains traceable.

## Architecture invariant

**KoggerApp = acquisition/device/visualization shell.**  
**Navimetry = bathymetry, survey geometry, Survey-aware QC, surface generation and engineering export engine.**

The next integration milestone should connect the stabilized Navimetry engine behind the placeholder page through a narrow processing interface, not duplicate KLF parsing or device acquisition logic prematurely.

## Licensing / attribution

KoggerApp's existing GPLv3 license and upstream notices remain applicable to the KoggerApp-derived application. The vendor's separate permission for branding/use is compatible with this integration approach; keep the upstream license and attribution visible in the unified product.
