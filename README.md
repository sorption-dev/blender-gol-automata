# GOL Automata

A Blender 3.0+ add-on that grows a "manifesto tower" from Conway's Game of Life.
Every generation of the automaton becomes a layer of the tower; the Z axis is the
axis of time, so each next generation sits below the previous one. That axis can
also be any curve in the scene — then the tower bends along the path you drew.

Landing page and full documentation — **https://sorption.dev/p/golautomata**
(the site lives in a separate repository).

Licence — [GPL-3.0-or-later](LICENSE). Russian readme — [README_RU.md](README_RU.md).

## The rules of the game

The game runs on a field of cells — the "universe":

- in an empty (dead) cell with exactly 3 living neighbours, life is born;
- a living cell with 2 or 3 living neighbours keeps living;
- with fewer than 2 or more than 3 neighbours the cell dies, of loneliness or of
  overcrowding.

The starting drawing is up to the player — it is the embryo of the system.

## Installation

As a single file, like any classic add-on (Blender 3.0 and newer):
`Edit > Preferences > Add-ons > Install` → pick `gol_automata.py` → tick the box.
The panel appears in the 3D viewport: press `N` → the **GOL** tab.

From Blender 4.2 onwards it also builds as an extension — `blender_manifest.toml`
is in the repository:

```bash
blender --command extension build --source-dir . --output-dir dist
```

The result is `dist/gol_automata-1.0.0.zip`: code, manifest, licence and readme.
Python cache and dot-files stay out of the archive — that is what
`paths_exclude_pattern` in the manifest is for. Validate the manifest before
building with `blender --command extension validate .`

The built archive installs through `Install from Disk` in preferences, or with
`blender --command extension install-file --repo user_default --enable <zip>`.

## The panel

- **Generate Tower** — run the automaton and build the tower. **Replace Previous**
  clears the `GOL Automata` collection before each run, which makes iterating easy.
  Only objects created by the add-on are removed (they carry a `gol_generated`
  mark): your own objects placed in that collection, and the selected Instance
  Object, are left alone. Explosive rules on large grids stop automatically once
  the budget (~4M vertices) is reached, so Blender never freezes.
- **Seed** — universe size and the starting state:
  - *Random* — random fill (Density + Seed);
  - *Pattern* — classic patterns (Glider, R-Pentomino, Acorn, Pulsar, Gosper
    Glider Gun and more), stamped into the centre of the grid;
  - *Paint* — draw cells by hand. **Edit Seed** opens an editor: clicking a cell
    toggles it, there is X/Y symmetry, Clear/Invert/Random, and **Step** to advance
    the seed one generation. Opening the editor from Random/Pattern captures the
    current seed for hand-editing.
- **Rules** — rules in B/S (Birth/Survive) notation plus presets: Conway B3/S23,
  HighLife, Day & Night, Seeds, Maze, Coral and others. *Wrap Edges* closes the
  field into a torus. *Stop on Repeat* halts the tower once the universe has
  cycled. *Noise* sparks single cells along the rim of the colony on every step,
  so the universe stops fading out and the tower reaches its full height.
- **Tower** — generation count, voxel size, cell scale inside its slot, and the
  time axis: *Straight* is a vertical tower (downwards or upwards), *Curve* runs
  along a curve in the scene. In curve mode generations are strung along a Bezier,
  NURBS or Poly spline: *Fit to Curve* stretches the history across the whole
  path, *Keep Spacing* keeps the step of a straight tower, *Align Layers* turns
  the layers across the path, and a closed spline folds time into a ring.
- **Shape FX** — sculpting effects:
  - *Cell Spacing / Layer Spacing* — gaps between cells and between generations;
  - *Twist* — winds the tower into a spiral;
  - *Taper* — narrows it towards the last generation (a spire) or widens it;
  - *Age Scale* — long-lived cells grow or shrink;
  - *Jitter* — random per-cell offset for an organic, ruined look.
- **Output** — geometry style: cubes, **wax** (generations fused into one smooth
  sculpture: voxel Remesh + Smooth, non-destructive, so the detail can be dialled
  with the Wax Detail slider or straight in the modifiers), planes, points (for
  Geometry Nodes) or instances of any object. One mesh, or one object per
  generation. *Weld Cells* welds touching cells and removes interior faces (a
  clean shell for 3D printing). The `gol_generation` and `gol_age` attributes are
  written onto the vertices; *Color by Age* assigns a material with a gradient
  driven by cell age.
- **Animation** — the tower grows along the timeline: a Build modifier (single
  mesh) or visibility keyframes (object per generation).

## Tips

- A Glider on a grid with *Wrap Edges* draws an endless diagonal thread.
- Diehard on a grid of 30×30 or larger dies out at exactly generation 130 — a
  tower with a natural ending. On a smaller field the figure hits the walls and
  falls into a cycle instead of dying.
- The Seeds rule (B2/S) explodes — keep Cell Scale small.
- If the tower keeps breaking off before the height you asked for, switch on
  *Noise* with an Amount around 0.01.
- The `gol_age` attribute is available in shaders through the Attribute node and
  in Geometry Nodes through Named Attribute.

## Licence

GPL-3.0-or-later. Full text in the [LICENSE](LICENSE) file.

The add-on calls Blender's Python API, and Blender is distributed under the GPL,
so derivative works have to stay under a GPL-compatible licence. The official
extensions platform ([extensions.blender.org](https://extensions.blender.org/))
has accepted add-ons only under `GPL-3.0-or-later` since August 2024.

What that means in practice:

- you can use the add-on however you like, including commercial work;
- everything it generates belongs to you — the licence covers the code, not the
  result of running it;
- you can modify the code for yourself with no obligations whatsoever;
- when distributing it — including a modified version, and including for money —
  you have to ship the sources and keep the same licence.
