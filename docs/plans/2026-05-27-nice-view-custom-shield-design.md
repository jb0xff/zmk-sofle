# nice_view_custom shield — design

Date: 2026-05-27

## Goal

Bring the `nice_view` shield's source (especially the display art in `widgets/art.c`)
into this repo so it can be edited locally, without modifying the upstream `zmk`
checkout that west pulls in. Vendor as-is in this step; iterate on the art in a
later step.

## Approach

Add a self-contained shield at `boards/shields/nice_view_custom/`, mirroring
upstream `app/boards/shields/nice_view/` verbatim but renamed to avoid collision
with the still-present upstream shield. This repo's `build.yaml` switches its
`shield:` entries from `nice_view` to `nice_view_custom`. Immediately after
vendoring, builds should produce equivalent output to today, except `art.c` now
lives in this repo and is editable.

Source of truth: upstream `zmkfirmware/zmk` at the revision pinned in
`config/west.yml` (`abb64ba316c29caddc49727ca2cac2f0ed5970c7`).

## Files to vendor

From upstream `app/boards/shields/nice_view/` into
`boards/shields/nice_view_custom/`, preserving structure:

- `Kconfig.shield`, `Kconfig.defconfig`
- `CMakeLists.txt`
- `nice_view.overlay` → `nice_view_custom.overlay`
- any per-board overlays under `boards/`
- `widgets/art.c`, `widgets/art.h`
- `widgets/status.c`, `widgets/status.h`
- `widgets/peripheral_status.c`

## Renames

- Shield name: `nice_view` → `nice_view_custom` (file basenames, overlay filename,
  any references in `CMakeLists.txt`).
- Kconfig symbol: `SHIELD_NICE_VIEW` → `SHIELD_NICE_VIEW_CUSTOM`.
- Any dependent widget Kconfig options get a `_CUSTOM` suffix as needed to avoid
  collision with the still-present upstream shield.

The exact rename list will be finalized during implementation, after the
upstream files are read (they aren't in the working tree until `west update`
runs).

## Build wiring

- `build.yaml`: replace each `shield: nice_view` with `shield: nice_view_custom`
  (3 entries: left, right, studio-left).
- `settings_reset` entries stay untouched.
- `config/west.yml` unchanged — upstream `zmk` stays pinned at `abb64ba…`.

## Verification

- `west build` succeeds for both `eyelash_sofle_left + nice_view_custom` and
  `eyelash_sofle_right + nice_view_custom`.
- Resulting UF2s flash and the displays show the stock ZMK art (proves vendoring
  is clean before any art changes).
- Sanity check: vendored `art.c` is byte-equal to upstream's at the pinned
  revision.

## Out of scope

- Actually changing the art. That's a follow-up.
- Updating the upstream `zmk` pin.
- Touching `settings_reset` or any other shield.

## Risks

- Kconfig symbol rename list is best-effort until upstream files are opened.
  Implementation must adjust to match reality.
- Vendoring decouples the widget code from future upstream changes — intentional,
  but worth noting.
