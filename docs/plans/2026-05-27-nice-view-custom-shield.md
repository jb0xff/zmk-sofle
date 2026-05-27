# nice_view_custom shield Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Vendor the upstream `nice_view` shield into this repo as `nice_view_custom` so the display art (`widgets/art.c`) is editable locally, without modifying the upstream `zmk` checkout.

**Architecture:** Self-contained shield at `boards/shields/nice_view_custom/`, mirroring upstream `app/boards/shields/nice_view/` verbatim with the shield name and Kconfig symbols suffixed `_CUSTOM`. `build.yaml` swaps its `shield: nice_view` entries to `shield: nice_view_custom`. Upstream `zmk` pin in `config/west.yml` stays as-is.

**Tech Stack:** ZMK firmware (Zephyr-based), Kconfig, Devicetree overlays, CMake. No language runtime tests; verification is build success + byte-equality of vendored files.

**Companion design doc:** `docs/plans/2026-05-27-nice-view-custom-shield-design.md`

**Key constraint:** The upstream `nice_view` shield source is not in this working tree — it lives in the `zmk` west-managed dependency. Task 1 fetches it at the pinned revision so subsequent tasks can read and copy real files. Several later tasks depend on what task 1 reveals (exact Kconfig symbol list, per-board overlays, CMake structure), so adjust those tasks based on the actual files.

---

## Task 1: Fetch upstream nice_view at the pinned revision

**Files:**
- Create: `/tmp/zmk-upstream/` (scratch, not committed)

**Step 1: Clone zmk at the pinned revision**

Run:
```bash
git clone --filter=blob:none --no-checkout https://github.com/zmkfirmware/zmk.git /tmp/zmk-upstream
git -C /tmp/zmk-upstream checkout abb64ba316c29caddc49727ca2cac2f0ed5970c7 -- app/boards/shields/nice_view
```

Expected: exit 0. `ls /tmp/zmk-upstream/app/boards/shields/nice_view` lists shield files.

**Step 2: Inventory the shield**

Run:
```bash
find /tmp/zmk-upstream/app/boards/shields/nice_view -type f | sort
```

Expected: a list including `Kconfig.shield`, `Kconfig.defconfig`, `CMakeLists.txt`, `nice_view.overlay`, `widgets/art.c`, `widgets/art.h`, `widgets/status.c`, `widgets/status.h`, `widgets/peripheral_status.c`, and possibly some `boards/*.overlay` files.

Record the exact file list — task 3 copies each one.

**Step 3: Identify Kconfig symbols to rename**

Run:
```bash
grep -rE 'SHIELD_NICE_VIEW|ZMK_WIDGET_NICE_VIEW|NICE_VIEW' /tmp/zmk-upstream/app/boards/shields/nice_view
```

Expected: a small set of Kconfig symbols and references. Record them — they all need `_CUSTOM` suffix renames in task 5.

**No commit** — this is exploratory only; `/tmp/zmk-upstream/` is scratch.

---

## Task 2: Create the shield directory skeleton

**Files:**
- Create: `boards/shields/nice_view_custom/` (empty dir, plus a `.gitkeep` only if needed — usually files in next task make this unnecessary)

**Step 1: Create directory**

Run:
```bash
mkdir -p boards/shields/nice_view_custom/widgets
```

**Step 2: Verify**

Run:
```bash
ls -la boards/shields/nice_view_custom/
```

Expected: empty `widgets/` subdir.

**No commit yet** — combined with task 3.

---

## Task 3: Copy upstream files verbatim into the new shield

**Files:**
- Create: every file listed in task 1 step 2, mirrored under `boards/shields/nice_view_custom/` with `nice_view.overlay` → `nice_view_custom.overlay` (and similarly any `boards/<name>.overlay` keeping its name but living under `nice_view_custom/boards/`).

**Step 1: Copy each file**

Run (adjust file list to match task 1 inventory):
```bash
SRC=/tmp/zmk-upstream/app/boards/shields/nice_view
DST=boards/shields/nice_view_custom

cp "$SRC/Kconfig.shield"     "$DST/Kconfig.shield"
cp "$SRC/Kconfig.defconfig"  "$DST/Kconfig.defconfig"
cp "$SRC/CMakeLists.txt"     "$DST/CMakeLists.txt"
cp "$SRC/nice_view.overlay"  "$DST/nice_view_custom.overlay"
cp "$SRC/widgets/art.c"      "$DST/widgets/art.c"
cp "$SRC/widgets/art.h"      "$DST/widgets/art.h"
cp "$SRC/widgets/status.c"   "$DST/widgets/status.c"
cp "$SRC/widgets/status.h"   "$DST/widgets/status.h"
cp "$SRC/widgets/peripheral_status.c" "$DST/widgets/peripheral_status.c"
```

If task 1 revealed per-board overlays under `$SRC/boards/`, copy them too:
```bash
mkdir -p "$DST/boards"
cp "$SRC/boards/"*.overlay "$DST/boards/"
```

**Step 2: Verify byte-equality of art.c**

Run:
```bash
diff "$SRC/widgets/art.c" "$DST/widgets/art.c" && echo "art.c matches upstream"
```

Expected: `art.c matches upstream`, exit 0.

**No commit yet** — file rename and Kconfig edits in next tasks finish the shield.

---

## Task 4: Rename the shield Kconfig symbol

**Files:**
- Modify: `boards/shields/nice_view_custom/Kconfig.shield`
- Modify: `boards/shields/nice_view_custom/Kconfig.defconfig`

**Step 1: Edit `Kconfig.shield`**

Replace every occurrence of `SHIELD_NICE_VIEW` with `SHIELD_NICE_VIEW_CUSTOM`. Replace any literal references to the shield name string `"nice_view"` used as a `def_bool $(shields_list_contains,nice_view)` test with `"nice_view_custom"`. (This is the line that ties the Kconfig symbol to the directory name.)

Use the Edit tool with the actual file contents read in task 1.

**Step 2: Edit `Kconfig.defconfig`**

Same rename: `SHIELD_NICE_VIEW` → `SHIELD_NICE_VIEW_CUSTOM` everywhere it appears.

If task 1 step 3 revealed extra Kconfig symbols (e.g. `ZMK_WIDGET_NICE_VIEW_STATUS`), add a `_CUSTOM` suffix to each one in BOTH `Kconfig.shield` and `Kconfig.defconfig`, AND in any `.c`/`.h` file that references them (next task).

**Step 3: Sanity grep**

Run:
```bash
grep -rE 'SHIELD_NICE_VIEW[^_]|"nice_view"' boards/shields/nice_view_custom/
```

Expected: no matches (every symbol now has `_CUSTOM` suffix, every string reference says `nice_view_custom`).

---

## Task 5: Update widget source references

**Files:**
- Modify: any file under `boards/shields/nice_view_custom/widgets/` that references the upstream Kconfig symbols or shield name.

**Step 1: Find references**

Run:
```bash
grep -rnE 'NICE_VIEW|nice_view' boards/shields/nice_view_custom/widgets/ boards/shields/nice_view_custom/CMakeLists.txt boards/shields/nice_view_custom/nice_view_custom.overlay
```

Common cases:
- `#if IS_ENABLED(CONFIG_ZMK_WIDGET_NICE_VIEW_STATUS)` → add `_CUSTOM` to match renamed symbol.
- `CMakeLists.txt` references like `target_sources(... widgets/status.c)` — paths likely unchanged, but verify.
- `nice_view_custom.overlay` — usually no symbol renames needed (it defines hardware), but check.

**Step 2: Apply renames with Edit**

Update every reference uncovered in step 1 to use the `_CUSTOM` symbols.

**Step 3: Sanity grep again**

Run:
```bash
grep -rE 'CONFIG_(SHIELD_)?ZMK_WIDGET_NICE_VIEW[^_]|CONFIG_SHIELD_NICE_VIEW[^_]' boards/shields/nice_view_custom/
```

Expected: no matches.

---

## Task 6: Commit the vendored shield (before flipping build.yaml)

**Step 1: Stage**

Run:
```bash
git add boards/shields/nice_view_custom/
git status
```

Expected: all new files under `boards/shields/nice_view_custom/` staged; nothing else.

**Step 2: Commit**

```bash
git commit -m "$(cat <<'EOF'
Add nice_view_custom shield vendored from upstream

Copies app/boards/shields/nice_view from zmkfirmware/zmk at the revision
pinned in config/west.yml (abb64ba…), renaming the shield to
nice_view_custom and the Kconfig symbols accordingly. Builds still use
the upstream nice_view shield; the switch happens in the next commit.
EOF
)"
```

**Step 3: Verify**

Run: `git log --oneline -1`
Expected: the new commit appears at HEAD.

---

## Task 7: Switch build.yaml to nice_view_custom

**Files:**
- Modify: `build.yaml`

**Step 1: Edit**

In `build.yaml`, replace every `shield: nice_view` with `shield: nice_view_custom`. There are 3 such lines (left, right, studio-left). Leave the two `shield: settings_reset` lines untouched.

**Step 2: Verify the diff**

Run:
```bash
git diff build.yaml
```

Expected: exactly 3 lines changed, all `nice_view` → `nice_view_custom`. No other diffs.

---

## Task 8: Verify the build

There are two paths depending on whether the user has a local west workspace.

**Step 1: Check for local west workspace**

Run:
```bash
[ -f .west/config ] && echo "local west workspace present" || echo "no local west workspace"
```

**Step 2a: If local west workspace exists, build both halves**

Run:
```bash
west build -p -s zmk/app -d build/left  -b eyelash_sofle_left  -- -DSHIELD=nice_view_custom -DZMK_EXTRA_MODULES="$(pwd)"
west build -p -s zmk/app -d build/right -b eyelash_sofle_right -- -DSHIELD=nice_view_custom -DZMK_EXTRA_MODULES="$(pwd)"
```

Expected: both builds exit 0. UF2 artifacts produced at `build/left/zephyr/zmk.uf2` and `build/right/zephyr/zmk.uf2`.

**Step 2b: If no local west workspace**

Stop and tell the user. Options:
1. Push the branch and rely on GitHub Actions (the project's normal build path) to verify.
2. Set up a local west workspace (one-time): `west init -l config && west update`.

Do not proceed past this task without a green build somewhere.

**Step 3: Smoke-check the binary**

If built locally, confirm the UF2 is non-trivially sized (rough sanity, not exact):
```bash
ls -lh build/left/zephyr/zmk.uf2 build/right/zephyr/zmk.uf2
```

Expected: both files exist and are >100KB.

---

## Task 9: Commit the build.yaml switch

**Step 1: Stage and commit**

```bash
git add build.yaml
git commit -m "$(cat <<'EOF'
Switch builds to the nice_view_custom shield

Both halves and the studio-left build now use the in-repo
nice_view_custom shield instead of upstream nice_view, so future art
changes land in this repo without forking zmk. settings_reset builds
are unchanged.
EOF
)"
```

**Step 2: Verify**

Run: `git log --oneline -3`
Expected: build.yaml switch on top, shield vendoring below it, design doc below that.

---

## Task 10: Cleanup

**Step 1: Remove scratch clone**

Run:
```bash
rm -rf /tmp/zmk-upstream
```

**Step 2: Confirm working tree clean**

Run:
```bash
git status
```

Expected: `nothing to commit, working tree clean`.

---

## Done criteria

- `boards/shields/nice_view_custom/` exists with all upstream files mirrored and Kconfig symbols renamed.
- `build.yaml` references `nice_view_custom` for the 3 nice_view entries; settings_reset entries untouched.
- A build (local or CI) of both halves with the new shield succeeds.
- `widgets/art.c` is byte-equal to upstream `nice_view/widgets/art.c` at the pinned revision (sanity check from task 3 step 2).
- Three commits on `main`: design doc (already landed), vendored shield, build.yaml switch.

## Follow-ups (not in this plan)

- Actually customize `widgets/art.c` with new artwork.
- Optionally extract reusable art-conversion tooling (e.g. an LVGL image converter snippet) into the repo.
