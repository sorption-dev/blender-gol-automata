# SPDX-FileCopyrightText: 2026 Sorption
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# GOL Automata - a tower generator built on the Game of Life cellular automaton.
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. This program is distributed WITHOUT ANY WARRANTY, without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. The
# full license text is in the LICENSE file next to this one and at
# https://www.gnu.org/licenses/gpl-3.0.html

bl_info = {
    "name": "GOL Automata",
    "author": "Sorption",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > GOL",
    "description": "Game of Life tower generator: generations stack along the Z axis into a voxel manifesto tower",
    "category": "Add Mesh",
}

import bpy
import bmesh
import math
import random as pyrandom
from mathutils import Matrix, Vector
from bpy.props import (
    StringProperty, BoolProperty, IntProperty, FloatProperty,
    EnumProperty, PointerProperty,
)
from bpy.types import Panel, Operator, PropertyGroup


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

# Largest grid that can still be drawn with buttons in the dialog
PAINT_LIMIT = 48

COLLECTION_NAME = "GOL Automata"
ATTR_GEN = "gol_generation"
ATTR_AGE = "gol_age"
ATTR_AGE_NORM = "gol_age_norm"
# Layer rotation on a curve: points cannot carry it, but instances need it
ATTR_ROT = "gol_rotation"

# Ceiling on the result size: guards against freezes and OOM on huge grids
MAX_VERTS = 4_000_000
# Above this face count welding (a python dict per face) is skipped
MAX_WELD_FACES = 500_000

# Rule presets: (birth, survive)
RULE_PRESETS = {
    'CONWAY':    ("3", "23"),
    'HIGHLIFE':  ("36", "23"),
    'DAYNIGHT':  ("3678", "34678"),
    'SEEDS':     ("2", ""),
    'MAZE':      ("3", "12345"),
    'CORAL':     ("3", "45678"),
    'TWOBYTWO':  ("36", "125"),
    'NODEATH':   ("3", "012345678"),
}

# Classic patterns. '#' is a living cell. Stamped into the centre of the grid.
PATTERNS = {
    'GLIDER': (
        "_#_",
        "__#",
        "###",
    ),
    'RPENTOMINO': (
        "_##",
        "##_",
        "_#_",
    ),
    'LWSS': (
        "#__#_",
        "____#",
        "#___#",
        "_####",
    ),
    'DIEHARD': (
        "______#_",
        "##______",
        "_#___###",
    ),
    'ACORN': (
        "_#_____",
        "___#___",
        "##__###",
    ),
    'PULSAR': (
        "__###___###__",
        "_____________",
        "#____#_#____#",
        "#____#_#____#",
        "#____#_#____#",
        "__###___###__",
        "_____________",
        "__###___###__",
        "#____#_#____#",
        "#____#_#____#",
        "#____#_#____#",
        "_____________",
        "__###___###__",
    ),
    'PENTADECATHLON': (
        "__#____#__",
        "##_####_##",
        "__#____#__",
    ),
    'GOSPER_GUN': (
        "________________________#___________",
        "______________________#_#___________",
        "____________##______##____________##",
        "___________#___#____##____________##",
        "##________#_____#___##______________",
        "##________#___#_##____#_#___________",
        "__________#_____#_______#___________",
        "___________#___#____________________",
        "____________##______________________",
    ),
}

# Cube template: vertices in the [-1, 1] range, faces with outward normals
CUBE_VERTS = (
    (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
    (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
)
CUBE_FACES = (
    (0, 3, 2, 1), (4, 5, 6, 7),
    (0, 1, 5, 4), (1, 2, 6, 5),
    (2, 3, 7, 6), (3, 0, 4, 7),
)


# ----------------------------------------------------------------------------
# Simulation
# ----------------------------------------------------------------------------

def noise_hash(x, y, gen, seed):
    """Pseudo-random number in 0..1 from cell coordinates, generation and seed.

    A hash rather than a generator: the value does not depend on the order in
    which a set is traversed, so the same settings always give exactly the same
    tower. The finalizer is borrowed from MurmurHash3 - it mixes the low bits,
    without which a regular lattice would show through instead of noise."""
    h = (x * 0x1F1F1F1F + y * 0x9E3779B1 + gen * 0x85EBCA77 + seed * 0xC2B2AE3D) & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h / 4294967296.0


def gol_step(live, w, h, birth, survive, wrap, noise=0.0, gen=0, noise_seed=0):
    """One step of the cellular automaton. live is a set of (x, y) coordinates.
    Sparse algorithm: only living cells and their neighbours are visited.
    noise is the chance for an empty cell at the colony rim to spark by itself,
    which stops life from collapsing into still figures and cycles."""
    counts = {}
    for x, y in live:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if wrap:
                    nx %= w
                    ny %= h
                elif not (0 <= nx < w and 0 <= ny < h):
                    continue
                key = (nx, ny)
                counts[key] = counts.get(key, 0) + 1
    nxt = set()
    for cell, n in counts.items():
        if cell in live:
            if n in survive:
                nxt.add(cell)
        elif n in birth:
            nxt.add(cell)
    # Living cells without a single neighbour never make it into counts,
    # which matters for S0-style rules (Life without Death)
    if 0 in survive:
        for cell in live:
            if cell not in counts:
                nxt.add(cell)
    if noise > 0.0:
        # Noise only ignites, and only at the colony rim (counts holds exactly
        # that rim). Flipping the state symmetrically was tried: on a small
        # colony it kills more than it creates, and the pattern dies twice as
        # fast as with no noise at all. Scanning the whole grid is pointless
        # too - the universe would turn into an even ripple.
        zone = counts.keys() - nxt
        sparks = {c for c in zone
                  if noise_hash(c[0], c[1], gen, noise_seed) < noise}
        if not sparks and zone:
            # A fading colony shrinks its own rim, so fewer sparks land and
            # the noise cannot rescue it in time. Hence at least one cell
            # always ignites: with that the universe no longer collapses
            sparks = {min(zone, key=lambda c: noise_hash(c[0], c[1], gen, noise_seed))}
        nxt |= sparks
    return nxt


def simulate(seed, w, h, birth, survive, wrap, max_layers, stop_loop,
             cell_budget=None, wm=None, noise=0.0, noise_seed=0):
    """Runs the automaton. Returns (layers, ages, reason):
    layers - list of sets of living cells, one per generation,
    ages   - list of dicts {(x, y): generations the cell has been alive},
    reason - why it stopped (for the report in the panel).
    cell_budget caps the total cell count as a guard against freezes and OOM
    on explosive rules; it is checked inside the loop rather than after it,
    or the simulation itself would already have eaten the memory."""
    layers = [set(seed)]
    ages = [{c: 1 for c in seed}]
    seen = {frozenset(seed)}
    total = len(seed)
    reason = "reached generation limit"
    for gen in range(1, max_layers):
        nxt = gol_step(layers[-1], w, h, birth, survive, wrap,
                       noise=noise, gen=gen, noise_seed=noise_seed)
        if not nxt:
            reason = "extinct at generation %d" % gen
            break
        if stop_loop:
            key = frozenset(nxt)
            if key in seen:
                reason = "cycle detected at generation %d" % gen
                break
            seen.add(key)
        total += len(nxt)
        if cell_budget is not None and total > cell_budget:
            reason = "cell budget reached at generation %d — lower grid/generations" % gen
            break
        prev = ages[-1]
        ages.append({c: prev.get(c, 0) + 1 for c in nxt})
        layers.append(nxt)
        if wm is not None:
            wm.progress_update(gen)
    return layers, ages, reason


# ----------------------------------------------------------------------------
# Seed: storage and helpers
# ----------------------------------------------------------------------------
# The starting state lives in scene.gol.seed_data as a string of '0'/'1'
# (row-major, row 0 is the top of the grid). seed_w/seed_h remember the size
# the string was built for, so that resizing the grid re-centres the drawing
# instead of losing it.

def ensure_seed(gol):
    """Brings seed_data in line with the current grid size."""
    w, h = gol.grid_x, gol.grid_y
    if gol.seed_w == w and gol.seed_h == h and len(gol.seed_data) == w * h:
        return
    remap_seed(gol)


def remap_seed(gol):
    """Re-centres an older drawing into a grid of the new size."""
    ow, oh = gol.seed_w, gol.seed_h
    nw, nh = gol.grid_x, gol.grid_y
    old = gol.seed_data
    if len(old) != ow * oh:
        old = "0" * (ow * oh)
    # int(x / 2) truncation rather than floor: growing and shrinking the grid
    # must be mutually inverse, or every slider step drifts the drawing aside
    offx = int((nw - ow) / 2)
    offy = int((nh - oh) / 2)
    buf = []
    for y in range(nh):
        oy = y - offy
        for x in range(nw):
            ox = x - offx
            if 0 <= ox < ow and 0 <= oy < oh:
                buf.append(old[oy * ow + ox])
            else:
                buf.append("0")
    gol.seed_data = "".join(buf)
    gol.seed_w = nw
    gol.seed_h = nh


def cells_to_string(cells, w, h):
    return "".join("1" if (i % w, i // w) in cells else "0" for i in range(w * h))


def string_to_cells(data, w):
    return {(i % w, i // w) for i, c in enumerate(data) if c == "1"}


def seed_cells(gol, report=None):
    """Builds the first generation according to the chosen mode."""
    w, h = gol.grid_x, gol.grid_y
    live = set()
    if gol.seed_mode == 'RANDOM':
        rng = pyrandom.Random(gol.rand_seed)
        for y in range(h):
            for x in range(w):
                if rng.random() < gol.density:
                    live.add((x, y))
    elif gol.seed_mode == 'PATTERN':
        rows = PATTERNS[gol.pattern]
        ph = len(rows)
        pw = max(len(r) for r in rows)
        if (pw > w or ph > h) and report:
            report({'WARNING'}, "Pattern %dx%d does not fit grid %dx%d — clipped" % (pw, ph, w, h))
        offx = (w - pw) // 2
        offy = (h - ph) // 2
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch == "#":
                    x, y = offx + i, offy + j
                    if 0 <= x < w and 0 <= y < h:
                        live.add((x, y))
    else:  # PAINT
        ensure_seed(gol)
        live = string_to_cells(gol.seed_data, w)
    return live


def parse_rules(gol):
    birth = {int(c) for c in gol.rule_birth}
    survive = {int(c) for c in gol.rule_survive}
    return birth, survive


# ----------------------------------------------------------------------------
# Property callbacks
# ----------------------------------------------------------------------------

_updating = False  # guards against recursion between preset and text fields


def _grid_size_update(self, context):
    remap_seed(self)


def _sanitize_rule(text, allowed):
    digits = sorted({c for c in text if c in allowed})
    return "".join(digits)


def _rule_preset_update(self, context):
    global _updating
    if _updating or self.rule_preset == 'CUSTOM':
        return
    b, s = RULE_PRESETS[self.rule_preset]
    _updating = True
    try:
        self.rule_birth = b
        self.rule_survive = s
    finally:
        _updating = False


def _rule_text_update(self, context):
    global _updating
    if _updating:
        return
    _updating = True
    try:
        b = _sanitize_rule(self.rule_birth, "12345678")
        s = _sanitize_rule(self.rule_survive, "012345678")
        if self.rule_birth != b:
            self.rule_birth = b
        if self.rule_survive != s:
            self.rule_survive = s
        # Highlight the preset when the ruleset matches a known one
        for key, (pb, ps) in RULE_PRESETS.items():
            if pb == b and ps == s:
                if self.rule_preset != key:
                    self.rule_preset = key
                break
        else:
            if self.rule_preset != 'CUSTOM':
                self.rule_preset = 'CUSTOM'
    finally:
        _updating = False


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

def _curve_poll(self, obj):
    return obj.type == 'CURVE'


class GOLSettings(PropertyGroup):
    # --- Seed ---
    seed_mode: EnumProperty(
        name="Seed Mode",
        description="How the first generation (the embryo of the tower) is defined",
        items=[
            ('RANDOM', "Random", "Random fill controlled by density and a random seed — noisy, chaotic towers"),
            ('PATTERN', "Pattern", "A classic Game of Life pattern stamped at the grid center — structured towers"),
            ('PAINT', "Paint", "Hand-drawn cells — open the Seed Editor and click cells"),
        ],
        default='PATTERN',
    )
    grid_x: IntProperty(
        name="Grid Width",
        description="Universe width in cells",
        default=24, min=2, soft_max=64, max=256,
        update=_grid_size_update,
    )
    grid_y: IntProperty(
        name="Grid Height",
        description="Universe height in cells",
        default=24, min=2, soft_max=64, max=256,
        update=_grid_size_update,
    )
    density: FloatProperty(
        name="Density",
        description="Probability for each cell of the first generation to be alive "
                    "(0.1-0.25 gives structured growth, higher gets noisy)",
        default=0.18, min=0.0, max=1.0, subtype='FACTOR',
    )
    rand_seed: IntProperty(
        name="Seed",
        description="Random seed — same number always gives the same universe",
        default=1, min=0,
    )
    pattern: EnumProperty(
        name="Pattern",
        description="Classic Game of Life pattern",
        items=[
            ('GLIDER', "Glider", "The famous 5-cell spaceship"),
            ('RPENTOMINO', "R-Pentomino", "Tiny 5-cell pattern with a chaotic 1000+ generation life"),
            ('ACORN', "Acorn", "7 cells that grow for over 5000 generations"),
            ('DIEHARD', "Diehard", "Vanishes completely after 130 generations"),
            ('LWSS', "Lightweight Spaceship", "Travels across the universe"),
            ('PULSAR', "Pulsar", "Large period-3 oscillator"),
            ('PENTADECATHLON', "Pentadecathlon", "Period-15 oscillator"),
            ('GOSPER_GUN', "Gosper Glider Gun", "Shoots gliders forever (needs a wide grid)"),
        ],
        default='RPENTOMINO',
    )
    # Internal storage for the hand-drawn seed
    seed_data: StringProperty(default="", options={'HIDDEN'})
    seed_w: IntProperty(default=0, options={'HIDDEN'})
    seed_h: IntProperty(default=0, options={'HIDDEN'})
    sym_x: BoolProperty(
        name="Symmetry X",
        description="Mirror painting across the vertical axis",
        default=False,
    )
    sym_y: BoolProperty(
        name="Symmetry Y",
        description="Mirror painting across the horizontal axis",
        default=False,
    )

    # --- Rules ---
    rule_preset: EnumProperty(
        name="Rule Preset",
        description="Well-known cellular automaton rules",
        items=[
            ('CONWAY', "Conway B3/S23", "The classic Game of Life"),
            ('HIGHLIFE', "HighLife B36/S23", "Like Life, plus self-replicating patterns"),
            ('DAYNIGHT', "Day & Night B3678/S34678", "Symmetric rule — inverted patterns behave the same"),
            ('SEEDS', "Seeds B2/S", "Every living cell dies each step — explosive growth"),
            ('MAZE', "Maze B3/S12345", "Grows maze-like corridors"),
            ('CORAL', "Coral B3/S45678", "Slow coral-like growth"),
            ('TWOBYTWO', "2x2 B36/S125", "Block-oriented rule"),
            ('NODEATH', "Life w/o Death B3/S012345678", "Cells never die — pure accumulation"),
            ('CUSTOM', "Custom", "Type your own B/S digits below"),
        ],
        default='CONWAY',
        update=_rule_preset_update,
    )
    rule_birth: StringProperty(
        name="Birth",
        description="Neighbour counts that create a new cell (digits 1-8)",
        default="3",
        update=_rule_text_update,
    )
    rule_survive: StringProperty(
        name="Survive",
        description="Neighbour counts that keep a cell alive (digits 0-8)",
        default="23",
        update=_rule_text_update,
    )
    wrap: BoolProperty(
        name="Wrap Edges",
        description="Connect opposite edges of the universe (torus topology)",
        default=False,
    )
    stop_loop: BoolProperty(
        name="Stop on Repeat",
        description="Stop when the universe repeats a previous state (still life or oscillator)",
        default=True,
    )
    use_noise: BoolProperty(
        name="Noise",
        description="Disturb the automaton a little on every step, so the universe does not "
                    "settle into still figures or a short cycle and the tower keeps living "
                    "all the way to its full height",
        default=False,
    )
    noise_amount: FloatProperty(
        name="Noise Amount",
        description="Chance for an empty cell at the colony edge to spark into life on every "
                    "step. Around 0.01 the tower keeps growing and stays airy; 0.03 and above "
                    "boils it into dense soup. Space far from the colony is never touched, and "
                    "at least one cell always sparks, so the universe cannot die out",
        default=0.01, min=0.0, soft_max=0.05, max=1.0, subtype='FACTOR',
    )

    # --- Tower ---
    generations: IntProperty(
        name="Generations",
        description="Maximum number of layers in the tower (including the seed layer)",
        default=48, min=1, soft_max=200, max=2000,
    )
    time_axis: EnumProperty(
        name="Time Axis",
        description="The line generations are strung along",
        items=[
            ('Z', "Straight", "Generations stack vertically — the classic tower",
             'EMPTY_SINGLE_ARROW', 0),
            ('CURVE', "Curve", "Generations follow a curve object — the tower bends along the path",
             'CURVE_BEZCURVE', 1),
        ],
        default='Z',
    )
    direction: EnumProperty(
        name="Direction",
        description="Where the time axis goes",
        items=[
            ('DOWN', "Down", "Next generation below the previous — the manifesto tower", 'TRIA_DOWN', 0),
            ('UP', "Up", "Next generation above the previous", 'TRIA_UP', 1),
        ],
        default='DOWN',
    )
    path_object: PointerProperty(
        name="Curve",
        description="Curve the tower grows along — Bezier, NURBS or Poly. "
                    "Only the first spline is used",
        type=bpy.types.Object,
        poll=_curve_poll,
    )
    path_fit: EnumProperty(
        name="Along Curve",
        description="How generations are spaced along the path",
        items=[
            ('FIT', "Fit to Curve",
             "Spread all generations evenly over the whole curve — Layer Spacing has no effect"),
            ('PITCH', "Keep Spacing",
             "Keep the layer step of the straight tower (Voxel Size × Layer Spacing). "
             "The tower may end before the curve does, or run past it — the tail then continues straight"),
        ],
        default='FIT',
    )
    path_align: BoolProperty(
        name="Align Layers",
        description="Turn every generation to face along the curve, so layers become slices across "
                    "the path. Off — layers stay horizontal and only follow the curve's position",
        default=True,
    )
    path_reverse: BoolProperty(
        name="Reverse Path",
        description="Start the tower at the other end of the curve",
        default=False,
    )
    voxel_size: FloatProperty(
        name="Voxel Size",
        description="Size of one cell slot — grid pitch in all axes",
        default=1.0, min=0.001, soft_max=10.0, subtype='DISTANCE',
    )
    scale_xy: FloatProperty(
        name="Cell Scale XY",
        description="Cell footprint relative to its slot: 1.0 — cells touch, less — gaps appear",
        default=1.0, min=0.05, max=1.0, subtype='FACTOR',
    )
    scale_z: FloatProperty(
        name="Cell Scale Z",
        description="Cell height relative to the layer step",
        default=1.0, min=0.05, max=1.0, subtype='FACTOR',
    )
    at_cursor: BoolProperty(
        name="Spawn at 3D Cursor",
        description="Place the tower at the 3D cursor instead of the world origin",
        default=True,
    )

    # --- Shape FX ---
    spread_xy: FloatProperty(
        name="Cell Spacing",
        description="Spread cells apart in XY: 1.0 — grid pitch equals voxel size, more — gaps between cells",
        default=1.0, min=0.25, soft_max=3.0,
    )
    layer_spacing: FloatProperty(
        name="Layer Spacing",
        description="Distance between generations along Z as a multiple of voxel size — raise it to float the layers apart",
        default=1.0, min=0.05, soft_max=5.0,
    )
    twist: FloatProperty(
        name="Twist",
        description="Rotate each generation around the tower axis — spiral manifesto",
        default=0.0, soft_min=-0.35, soft_max=0.35, min=-math.pi, max=math.pi,
        subtype='ANGLE',
    )
    taper: FloatProperty(
        name="Taper",
        description="Layer footprint scale at the last generation: <1 — spire, >1 — inverted pyramid, 0 — needle point",
        default=1.0, min=0.0, soft_max=2.0,
    )
    age_scale: FloatProperty(
        name="Age Scale",
        description="Long-living cells grow (positive) or shrink (negative)",
        default=0.0, min=-0.9, soft_max=1.0, max=3.0,
    )
    jitter: FloatProperty(
        name="Jitter",
        description="Random offset of every cell for an organic, ruined look (deterministic per Seed)",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR',
    )

    # --- Output ---
    style: EnumProperty(
        name="Style",
        description="What geometry each living cell becomes",
        items=[
            ('CUBES', "Cubes", "Solid voxel cubes", 'CUBE', 0),
            ('WAX', "Wax", "Generations fused into one smooth organic sculpture (voxel remesh)", 'META_BALL', 4),
            ('PLANES', "Planes", "Flat squares — light, graphic look", 'MESH_PLANE', 1),
            ('POINTS', "Points", "Vertices only — feed them to Geometry Nodes", 'VERTEXSEL', 2),
            ('OBJECT', "Object", "Instance any object on every cell (Geometry Nodes)", 'OBJECT_DATA', 3),
        ],
        default='CUBES',
    )
    wax_detail: FloatProperty(
        name="Wax Detail",
        description="Remesh voxel size as a fraction of cell size — smaller is finer (and heavier)",
        default=0.35, min=0.1, max=1.0, subtype='FACTOR',
    )
    instance_object: PointerProperty(
        name="Instance Object",
        description="Object placed on every living cell",
        type=bpy.types.Object,
    )
    combine: EnumProperty(
        name="Combine",
        description="How the layers are grouped",
        items=[
            ('SINGLE', "Single Mesh", "One mesh for the whole tower — clean and fast"),
            ('PER_GEN', "Object per Generation", "Each generation is its own object under an empty — easy to animate and edit"),
        ],
        default='SINGLE',
    )
    weld: BoolProperty(
        name="Weld Cells",
        description="Merge touching cells and remove hidden interior faces — a clean printable shell",
        default=False,
    )
    replace_previous: BoolProperty(
        name="Replace Previous",
        description="Clear the GOL Automata collection before generating — perfect for iterating",
        default=True,
    )
    add_attributes: BoolProperty(
        name="Cell Attributes",
        description="Store 'gol_generation' and 'gol_age' integer attributes on vertices for shading and Geometry Nodes",
        default=True,
    )
    color_by_age: BoolProperty(
        name="Color by Age",
        description="Assign a material that colors cells by how long they have been alive",
        default=True,
    )
    material: PointerProperty(
        name="Material",
        description="Material used for the tower (leave empty to auto-create an age color ramp)",
        type=bpy.types.Material,
    )

    # --- Animation ---
    animate: BoolProperty(
        name="Animate Growth",
        description="Reveal the tower generation by generation over the timeline",
        default=False,
    )
    frame_step: IntProperty(
        name="Frames per Generation",
        description="How many frames each generation takes to appear",
        default=2, min=1, soft_max=24,
    )

    # --- Report from the last run ---
    last_report: StringProperty(default="", options={'HIDDEN'})


# ----------------------------------------------------------------------------
# Geometry construction
# ----------------------------------------------------------------------------

def get_output_collection(context):
    """Returns (creating if needed) the GOL Automata collection at the scene
    root. Our own collection carries a custom property; when names collide the
    marked one wins."""
    root = context.scene.collection
    fallback = None
    for child in root.children:
        if child.name == COLLECTION_NAME or child.name.startswith(COLLECTION_NAME + "."):
            if child.get("gol_automata"):
                return child
            if fallback is None:
                fallback = child
    if fallback is not None:
        return fallback
    col = bpy.data.collections.new(COLLECTION_NAME)
    col["gol_automata"] = True
    root.children.link(col)
    return col


def clear_collection(col, keep=None):
    """Removes from the collection only objects created by the add-on (marked
    with 'gol_generated'), together with their mesh datablocks and any orphaned
    instance node groups. Returns how many foreign objects were skipped.
    keep is an object that must not be touched (the Instance Object)."""
    skipped = 0
    doomed_groups = set()
    for obj in list(col.objects):
        if keep is not None and obj == keep:
            continue
        if not obj.get("gol_generated"):
            skipped += 1
            continue
        for mod in obj.modifiers:
            if mod.type == 'NODES' and mod.node_group is not None:
                doomed_groups.add(mod.node_group)
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
    # GOL_Instances groups nothing references any more
    for ng in doomed_groups:
        if ng.users == 0:
            bpy.data.node_groups.remove(ng)
    return skipped


def curve_polyline(obj, n_layers):
    """Polyline along the curve's first spline, in world coordinates.

    The spline is copied into a throwaway curve with no bevel or extrude: then
    to_mesh() returns a clean line rather than a tube, and the vertex order
    matches the spline's point order. Bezier, NURBS and Poly are all handled
    the same way, with no need to evaluate each type by hand."""
    splines = obj.data.splines
    if not splines:
        raise ValueError("Curve '%s' has no splines" % obj.name)
    src = splines[0]
    n_pts = len(src.bezier_points) if src.type == 'BEZIER' else len(src.points)
    if n_pts < 2:
        raise ValueError("Curve '%s' needs at least two points" % obj.name)
    # Sampling density: a couple of points per generation. Any sparser and
    # several layers share one segment, share its tangent, and the path facets
    spans = n_pts if src.use_cyclic_u else n_pts - 1
    resolution = max(12, min(64, (2 * n_layers) // spans + 1))

    tmp = bpy.data.curves.new("GOL_tmp_path", 'CURVE')
    try:
        tmp.dimensions = '3D'
        dst = tmp.splines.new(src.type)
        if src.type == 'BEZIER':
            dst.bezier_points.add(n_pts - 1)
            for s, d in zip(src.bezier_points, dst.bezier_points):
                # FREE: Blender has already resolved the handles, so copy them
                # as they are; AUTO would recompute them and the shape would drift
                d.handle_left_type = d.handle_right_type = 'FREE'
                d.co, d.handle_left, d.handle_right = s.co, s.handle_left, s.handle_right
        else:
            dst.points.add(n_pts - 1)
            for s, d in zip(src.points, dst.points):
                d.co = s.co
            dst.order_u = src.order_u
            dst.use_endpoint_u = src.use_endpoint_u
            dst.use_bezier_u = src.use_bezier_u
        dst.use_cyclic_u = src.use_cyclic_u
        dst.resolution_u = resolution
        tmp_obj = bpy.data.objects.new("GOL_tmp_path", tmp)
        try:
            mesh = tmp_obj.to_mesh()
            mw = obj.matrix_world
            pts = [mw @ v.co.copy() for v in mesh.vertices]
            tmp_obj.to_mesh_clear()
        finally:
            bpy.data.objects.remove(tmp_obj)
    finally:
        bpy.data.curves.remove(tmp)

    # A cyclic spline arrives as an open list, so close it back to the start
    if src.use_cyclic_u and pts and (pts[0] - pts[-1]).length > 1e-9:
        pts.append(pts[0].copy())
    return pts, src.use_cyclic_u


def path_frames(gol, n_layers):
    """4x4 matrices, one per generation, moving a layer from its local
    coordinates onto a point of the curve. The layer's local Z axis follows the
    tangent (when alignment is on), while the transverse axis is carried along
    the path by parallel transport, so the tower does not wind up on its own
    around bends. Returns (frames, notes); raises ValueError on a bad curve."""
    obj = gol.path_object
    pts, cyclic = curve_polyline(obj, n_layers)
    if gol.path_reverse:
        pts.reverse()
    # Drop coincident points up front, or a zero-length segment would be left
    # without a tangent
    clean = pts[:1]
    for p in pts[1:]:
        if (p - clean[-1]).length > 1e-9:
            clean.append(p)
    pts = clean
    seg = [(b - a).length for a, b in zip(pts, pts[1:])]
    total = sum(seg)
    if total <= 1e-6:
        raise ValueError("Curve '%s' has zero length" % obj.name)

    notes = []
    if len(obj.data.splines) > 1:
        notes.append("Curve '%s' has %d splines — using the first"
                     % (obj.name, len(obj.data.splines)))

    if n_layers > 1:
        if gol.path_fit == 'FIT':
            # On a cyclic curve the last layer must not land on the first
            step = total / (n_layers if cyclic else n_layers - 1)
        else:
            step = gol.voxel_size * gol.layer_spacing
    else:
        step = 0.0
    if (n_layers - 1) * step > total + 1e-6:
        notes.append("Tower is longer than the curve — the tail continues straight")

    frames = []
    up = None
    prev_tan = None
    i = 0        # index of the current polyline segment
    run = 0.0    # path length up to its start
    for k in range(n_layers):
        s = k * step
        while i < len(seg) - 1 and run + seg[i] < s:
            run += seg[i]
            i += 1
        tan = (pts[i + 1] - pts[i]).normalized()
        # Past the end of the curve the tower continues along the tangent
        # instead of piling up on the last point: (s - run) simply exceeds the
        # segment length there
        pos = pts[i] + tan * (s - run)

        if up is None:
            ref = Vector((0.0, 0.0, 1.0)) if abs(tan.z) < 0.98 else Vector((0.0, 1.0, 0.0))
            up = (ref - tan * ref.dot(tan)).normalized()
        else:
            # Parallel transport: turn the transverse axis by exactly the angle
            # the tangent turned by, and not a degree more
            axis = prev_tan.cross(tan)
            if axis.length > 1e-9:
                up = Matrix.Rotation(prev_tan.angle(tan, 0.0), 3, axis.normalized()) @ up
            up = up - tan * up.dot(tan)     # clear the accumulated drift
            if up.length <= 1e-9:
                ref = Vector((0.0, 0.0, 1.0)) if abs(tan.z) < 0.98 else Vector((0.0, 1.0, 0.0))
                up = ref - tan * ref.dot(tan)
            up.normalize()
        prev_tan = tan

        if gol.path_align:
            xax, yax, zax = up, tan.cross(up), tan
        else:
            xax = Vector((1.0, 0.0, 0.0))
            yax = Vector((0.0, 1.0, 0.0))
            zax = Vector((0.0, 0.0, 1.0))
        frames.append(Matrix((
            (xax.x, yax.x, zax.x, pos.x),
            (xax.y, yax.y, zax.y, pos.y),
            (xax.z, yax.z, zax.z, pos.z),
            (0.0, 0.0, 0.0, 1.0),
        )))
    return frames, notes


def _apply_frame(verts, start, m):
    """Moves the layer vertices verts[start:] onto the curve. Matrix components
    are unpacked once per layer: Matrix @ Vector on every vertex is noticeably
    more expensive, and there can be millions of vertices here."""
    m00, m01, m02, m03 = m[0]
    m10, m11, m12, m13 = m[1]
    m20, m21, m22, m23 = m[2]
    for i in range(start, len(verts)):
        X, Y, Z = verts[i]
        verts[i] = (m00 * X + m01 * Y + m02 * Z + m03,
                    m10 * X + m11 * Y + m12 * Z + m13,
                    m20 * X + m21 * Y + m22 * Z + m23)


def build_mesh(name, cells, gol, total_layers, max_age, gen_origin=0, frames=None):
    """Builds a mesh from cells. cells is a sorted list of (x, y, gen, age).
    gen_origin is subtracted from gen for the 'object per generation' mode,
    where the layer sits at the local origin and the object is offset along Z.
    Shape FX (twist/taper/jitter/age scale) are computed from the absolute gen
    so that spiral layers line up in both layout modes.
    frames (time axis along a curve) is one matrix per generation: the layer is
    built flat in local coordinates and the matrix puts it in place."""
    pitch = gol.voxel_size
    px = pitch * gol.spread_xy          # XY grid pitch
    pz = pitch * gol.layer_spacing      # step between generations
    # For Wax the cubes are inflated: diagonal and stepped neighbours then
    # overlap in volume and Remesh fuses them into a single form
    inflate = 1.3 if gol.style == 'WAX' else 1.0
    hxy = pitch * gol.scale_xy * 0.5 * inflate   # cell half-size (independent of
    hz = pitch * gol.scale_z * 0.5 * inflate     # spacing, so spacing opens gaps)
    w, h = gol.grid_x, gol.grid_y
    zdir = -1.0 if gol.direction == 'DOWN' else 1.0
    style = gol.style
    want_attrs = gol.add_attributes or gol.color_by_age
    use_jitter = gol.jitter > 0.0
    use_frames = frames is not None
    # Points and instances have no geometry to freeze the layer rotation into,
    # so it goes to an attribute that Geometry Nodes reads
    want_rot = use_frames and style in ('POINTS', 'OBJECT')
    # A separate rng so that jitter stays deterministic per Seed
    rng = pyrandom.Random(gol.rand_seed * 7919 + 13 + gen_origin)

    verts = []
    faces = []
    v_gen = []
    v_age = []
    v_rot = []
    cur_gen = -1        # generation whose vertices are piling up at the end of verts
    frame_start = 0
    frame_rot = (0.0, 0.0, 0.0)
    gen_norm = 1.0 / (total_layers - 1) if total_layers > 1 else 0.0
    age_norm = 1.0 / (max_age - 1) if max_age > 1 else 0.0
    for x, y, gen, age in cells:
        if use_frames and gen != cur_gen:
            # Layer finished: put it on the curve and start the next one
            if cur_gen >= 0:
                _apply_frame(verts, frame_start, frames[cur_gen])
            cur_gen = gen
            frame_start = len(verts)
            if want_rot:
                e = frames[gen].to_euler()
                frame_rot = (e.x, e.y, e.z)
        # Taper: layer scale from 1.0 at the seed to gol.taper at the last generation
        lscale = 1.0 + (gol.taper - 1.0) * (gen * gen_norm)
        # Age Scale: cell size multiplier driven by its age
        smult = max(1.0 + gol.age_scale * ((age - 1) * age_norm), 0.05)
        cx = (x - (w - 1) * 0.5) * px * lscale
        # Row 0 of the seed is the top of the grid, which is +Y in the world (matching the top view)
        cy = ((h - 1) * 0.5 - y) * px * lscale
        # On a curve the frame matrix sets the layer height; locally it is flat
        cz = 0.0 if use_frames else zdir * (gen - gen_origin) * pz
        if use_jitter:
            cx += (rng.random() - 0.5) * gol.jitter * px
            cy += (rng.random() - 0.5) * gol.jitter * px
            cz += (rng.random() - 0.5) * gol.jitter * pz * 0.5
        # Twist: rotation of the whole layer around the tower axis
        angle = gol.twist * gen
        ca, sa = math.cos(angle), math.sin(angle)
        hx2 = hxy * lscale * smult
        hz2 = hz * smult
        base = len(verts)
        if style in ('CUBES', 'WAX'):
            for vx, vy, vz in CUBE_VERTS:
                X = cx + vx * hx2
                Y = cy + vy * hx2
                verts.append((X * ca - Y * sa, X * sa + Y * ca, cz + vz * hz2))
            for f in CUBE_FACES:
                faces.append((base + f[0], base + f[1], base + f[2], base + f[3]))
            n = 8
        elif style == 'PLANES':
            for vx, vy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                X = cx + vx * hx2
                Y = cy + vy * hx2
                verts.append((X * ca - Y * sa, X * sa + Y * ca, cz))
            faces.append((base, base + 1, base + 2, base + 3))
            n = 4
        else:  # POINTS / OBJECT
            verts.append((cx * ca - cy * sa, cx * sa + cy * ca, cz))
            n = 1
        if want_attrs:
            v_gen.extend([gen] * n)
            v_age.extend([age] * n)
        if want_rot:
            v_rot.extend(frame_rot)     # n == 1 for points and instances
    if use_frames and cur_gen >= 0:
        _apply_frame(verts, frame_start, frames[cur_gen])

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    mesh.update()
    if want_attrs and verts:
        attr = mesh.attributes.new(ATTR_GEN, 'INT', 'POINT')
        attr.data.foreach_set("value", v_gen)
        attr = mesh.attributes.new(ATTR_AGE, 'INT', 'POINT')
        attr.data.foreach_set("value", v_age)
        # The normalised 0..1 age is baked into the mesh so the shared material
        # never has to be retuned per tower
        attr = mesh.attributes.new(ATTR_AGE_NORM, 'FLOAT', 'POINT')
        attr.data.foreach_set("value", [(a - 1) * age_norm for a in v_age])
    if want_rot and verts:
        attr = mesh.attributes.new(ATTR_ROT, 'FLOAT_VECTOR', 'POINT')
        attr.data.foreach_set("vector", v_rot)
    return mesh


def weld_mesh(mesh, merge_dist):
    """Welds neighbouring cells: removes duplicate vertices and interior faces
    (after remove_doubles the inner walls become pairs of coincident faces, and
    both of them are deleted)."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=merge_dist)
    bm.verts.index_update()
    face_groups = {}
    for f in bm.faces:
        key = frozenset(v.index for v in f.verts)
        face_groups.setdefault(key, []).append(f)
    doomed = [f for group in face_groups.values() if len(group) > 1 for f in group]
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context='FACES')
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()


def get_age_material(gol):
    """Material with a gradient driven by cell age. It reads the normalised
    gol_age_norm attribute baked into the mesh, so the shared material never
    needs retuning and older towers are not repainted by later runs.
    If the user supplied their own material, it is used as is."""
    if gol.material is not None:
        return gol.material
    mat = bpy.data.materials.get("GOL_AgeRamp")
    if mat is not None:
        # A material from an older add-on version (reading raw gol_age) is rebuilt
        ok = mat.use_nodes and any(
            n.bl_idname == 'ShaderNodeAttribute' and n.attribute_name == ATTR_AGE_NORM
            for n in mat.node_tree.nodes)
        if ok:
            return mat
    else:
        mat = bpy.data.materials.new("GOL_AgeRamp")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (620, 0)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (320, 0)
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    ramp.location = (20, 0)
    ramp.color_ramp.elements[0].color = (0.012, 0.09, 0.28, 1.0)   # newborn: deep blue
    ramp.color_ramp.elements[1].color = (1.0, 0.72, 0.13, 1.0)     # long-lived: warm gold
    attr = nt.nodes.new('ShaderNodeAttribute')
    attr.location = (-220, 0)
    attr.attribute_name = ATTR_AGE_NORM
    nt.links.new(attr.outputs['Fac'], ramp.inputs['Fac'])
    nt.links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    return mat


def get_time_material(gol):
    """Material for Wax: a gradient over normalised height (Generated Z).
    Remesh destroys vertex attributes but not bounding-box coordinates, so the
    colouring follows the time axis and needs no retuning per tower."""
    if gol.material is not None:
        return gol.material
    mat = bpy.data.materials.get("GOL_TimeRamp")
    if mat is not None:
        return mat
    mat = bpy.data.materials.new("GOL_TimeRamp")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (620, 0)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (320, 0)
    bsdf.inputs['Roughness'].default_value = 0.35
    # Waxy subsurface effect (the input name depends on the Blender version)
    for key in ("Subsurface Weight", "Subsurface"):
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = 0.12
            break
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    ramp.location = (20, 0)
    # Bottom of the tower (late generations) is gold, the top (seed) is blue
    ramp.color_ramp.elements[0].color = (1.0, 0.72, 0.13, 1.0)
    ramp.color_ramp.elements[1].color = (0.012, 0.09, 0.28, 1.0)
    sep = nt.nodes.new('ShaderNodeSeparateXYZ')
    sep.location = (-180, 0)
    coord = nt.nodes.new('ShaderNodeTexCoord')
    coord.location = (-380, 0)
    nt.links.new(coord.outputs['Generated'], sep.inputs['Vector'])
    nt.links.new(sep.outputs['Z'], ramp.inputs['Fac'])
    nt.links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    return mat


def add_wax_modifiers(obj, gol):
    """Non-destructive 'wax' treatment: a voxel remesh fuses the inflated cubes
    into one solid form and Smooth finishes the waxy look."""
    mod = obj.modifiers.new("GOL Wax Remesh", 'REMESH')
    mod.mode = 'VOXEL'
    mod.voxel_size = max(gol.voxel_size * gol.wax_detail, 0.001)
    mod.use_smooth_shade = True
    mod = obj.modifiers.new("GOL Wax Smooth", 'SMOOTH')
    mod.factor = 0.8
    mod.iterations = 3


def build_instance_group(gol, use_rotation=False):
    """Geometry Nodes group: instances the chosen object onto the points.
    use_rotation turns instances by the layer attribute (the time axis follows
    a curve and the points have to rotate along with it)."""
    ng = bpy.data.node_groups.new("GOL_Instances", 'GeometryNodeTree')
    if hasattr(ng, "interface"):  # Blender 4.0+
        ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
        ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    else:  # Blender 3.x
        ng.inputs.new('NodeSocketGeometry', "Geometry")
        ng.outputs.new('NodeSocketGeometry', "Geometry")
    n_in = ng.nodes.new('NodeGroupInput')
    n_in.location = (-420, 0)
    n_out = ng.nodes.new('NodeGroupOutput')
    n_out.location = (420, 0)
    info = ng.nodes.new('GeometryNodeObjectInfo')
    info.location = (-420, -160)
    info.transform_space = 'ORIGINAL'
    info.inputs['Object'].default_value = gol.instance_object
    if 'As Instance' in info.inputs:
        info.inputs['As Instance'].default_value = True
    iop = ng.nodes.new('GeometryNodeInstanceOnPoints')
    iop.location = (0, 0)
    s = gol.voxel_size
    iop.inputs['Scale'].default_value = (s * gol.scale_xy, s * gol.scale_xy, s * gol.scale_z)
    if use_rotation:
        rot = ng.nodes.new('GeometryNodeInputNamedAttribute')
        rot.location = (-420, -360)
        rot.data_type = 'FLOAT_VECTOR'
        rot.inputs['Name'].default_value = ATTR_ROT
        # A vector fed into the Rotation socket is read as XYZ Euler angles
        ng.links.new(rot.outputs['Attribute'], iop.inputs['Rotation'])
    ng.links.new(n_in.outputs['Geometry'], iop.inputs['Points'])
    ng.links.new(info.outputs['Geometry'], iop.inputs['Instance'])
    ng.links.new(iop.outputs['Instances'], n_out.inputs['Geometry'])
    return ng


# ----------------------------------------------------------------------------
# Operators
# ----------------------------------------------------------------------------

class GOL_OT_generate(Operator):
    bl_idname = "gol.generate"
    bl_label = "Generate Tower"
    bl_description = ("Run the cellular automaton and build the tower: "
                      "each generation becomes a layer along the time axis")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        gol = context.scene.gol
        w, h = gol.grid_x, gol.grid_y
        birth, survive = parse_rules(gol)
        if not birth:
            self.report({'ERROR'}, "Birth rule is empty — nothing can ever be born")
            return {'CANCELLED'}
        if gol.style == 'OBJECT' and gol.instance_object is None:
            self.report({'ERROR'}, "Pick an object to instance (Output > Instance Object)")
            return {'CANCELLED'}
        if gol.time_axis == 'CURVE' and (gol.path_object is None
                                         or gol.path_object.type != 'CURVE'):
            self.report({'ERROR'}, "Pick a curve for the time axis (Tower > Curve)")
            return {'CANCELLED'}

        seed = seed_cells(gol, self.report)
        if not seed:
            self.report({'ERROR'}, "Seed is empty — add some living cells first")
            return {'CANCELLED'}

        # Cell budget: keeps the result below MAX_VERTS vertices
        verts_per_cell = {'CUBES': 8, 'PLANES': 4}.get(gol.style, 1)
        cell_budget = MAX_VERTS // verts_per_cell

        wm = context.window_manager
        wm.progress_begin(0, gol.generations)
        try:
            layers, ages, reason = simulate(
                seed, w, h, birth, survive, gol.wrap, gol.generations,
                gol.stop_loop, cell_budget=cell_budget, wm=wm,
                noise=gol.noise_amount if gol.use_noise else 0.0,
                noise_seed=gol.rand_seed)

            total = sum(len(layer) for layer in layers)
            max_age = max(max(a.values()) for a in ages)
            notes = []

            # Frames are computed before clearing the collection: an unusable
            # curve must not cost the user the tower they already have
            frames = None
            if gol.time_axis == 'CURVE':
                try:
                    frames, path_notes = path_frames(gol, len(layers))
                except ValueError as err:
                    self.report({'ERROR'}, str(err))
                    return {'CANCELLED'}
                notes.extend(path_notes)

            col = get_output_collection(context)
            if gol.replace_previous:
                keep = gol.instance_object if gol.style == 'OBJECT' else None
                skipped = clear_collection(col, keep=keep)
                if skipped:
                    notes.append("Kept %d object(s) not created by GOL Automata" % skipped)

            if frames is not None:
                # The start of the curve is the start of the tower: the object
                # origin moves there and the frames are rebased onto it. The 3D
                # cursor plays no part here - the curve sets the position
                origin = frames[0].translation.copy()
                shift = Matrix.Translation(-origin)
                frames = [shift @ f for f in frames]
            else:
                origin = context.scene.cursor.location.copy() if gol.at_cursor else Vector((0.0, 0.0, 0.0))
            zdir = -1.0 if gol.direction == 'DOWN' else 1.0
            frame0 = context.scene.frame_current
            do_weld = gol.weld and gol.style in {'CUBES', 'PLANES'}
            # Per-generation objects carry the rotation themselves; a separate
            # attribute is only needed when the whole tower is one mesh of points
            use_rot = frames is not None and gol.path_align and gol.combine == 'SINGLE'
            instance_group = build_instance_group(gol, use_rot) if gol.style == 'OBJECT' else None
            created = []
            n_layers = len(layers)

            def weld_if_ok(mesh):
                if not do_weld:
                    return
                if len(mesh.polygons) > MAX_WELD_FACES:
                    if not any(n.startswith("Weld skipped") for n in notes):
                        notes.append("Weld skipped: over %dk faces" % (MAX_WELD_FACES // 1000))
                    return
                weld_mesh(mesh, gol.voxel_size * 1e-4)

            if gol.combine == 'SINGLE':
                # Sorting gives deterministic jitter and orders faces/vertices
                # by generation for the Build modifier
                cells = [(x, y, g, ages[g][(x, y)])
                         for g, layer in enumerate(layers) for (x, y) in sorted(layer)]
                mesh = build_mesh("GOL_Tower", cells, gol, n_layers, max_age, frames=frames)
                weld_if_ok(mesh)
                root = bpy.data.objects.new("GOL_Tower", mesh)
                root["gol_generated"] = True
                root.location = origin
                col.objects.link(root)
                created.append(root)
                if gol.animate:
                    # Build goes first in the stack: for the OBJECT style it has
                    # to assemble the points before the node modifier instances
                    # them, and for Wax before the remesh (growing wax)
                    mod = root.modifiers.new("GOL Build", 'BUILD')
                    mod.frame_start = frame0
                    mod.frame_duration = max(1, n_layers * gol.frame_step)
                if gol.style == 'WAX':
                    add_wax_modifiers(root, gol)
                if instance_group is not None:
                    mod = root.modifiers.new("GOL Instances", 'NODES')
                    mod.node_group = instance_group
            else:  # PER_GEN
                root = bpy.data.objects.new("GOL_Tower", None)
                root["gol_generated"] = True
                root.empty_display_type = 'PLAIN_AXES'
                root.empty_display_size = gol.voxel_size
                root.location = origin
                col.objects.link(root)
                created.append(root)
                for g, layer in enumerate(layers):
                    cells = [(x, y, g, ages[g][(x, y)]) for (x, y) in sorted(layer)]
                    mesh = build_mesh("GOL_Gen_%03d" % g, cells, gol, n_layers, max_age, gen_origin=g)
                    weld_if_ok(mesh)
                    ob = bpy.data.objects.new("GOL_Gen_%03d" % g, mesh)
                    ob["gol_generated"] = True
                    ob.parent = root
                    # The layer is built flat at the origin: either the curve
                    # frame or a plain vertical offset puts it in place
                    if frames is not None:
                        ob.matrix_local = frames[g]
                    else:
                        ob.location = (0.0, 0.0, zdir * g * gol.voxel_size * gol.layer_spacing)
                    col.objects.link(ob)
                    created.append(ob)
                    if gol.style == 'WAX':
                        add_wax_modifiers(ob, gol)
                    if instance_group is not None:
                        mod = ob.modifiers.new("GOL Instances", 'NODES')
                        mod.node_group = instance_group
                    if gol.animate:
                        frame = frame0 + g * gol.frame_step
                        if g > 0:
                            ob.hide_viewport = True
                            ob.hide_render = True
                            ob.keyframe_insert('hide_viewport', frame=frame - 1)
                            ob.keyframe_insert('hide_render', frame=frame - 1)
                        ob.hide_viewport = False
                        ob.hide_render = False
                        ob.keyframe_insert('hide_viewport', frame=frame)
                        ob.keyframe_insert('hide_render', frame=frame)

            if gol.color_by_age and gol.style in {'CUBES', 'PLANES', 'WAX'}:
                # Wax: attributes do not survive Remesh, so colour by height instead
                mat = get_time_material(gol) if gol.style == 'WAX' else get_age_material(gol)
                for ob in created:
                    if ob.type == 'MESH':
                        ob.data.materials.append(mat)

            for ob in context.selected_objects:
                ob.select_set(False)
            # The collection may be excluded from the view layer, and then the
            # object cannot be selected (select_set raises RuntimeError)
            if root.name in context.view_layer.objects:
                root.select_set(True)
                context.view_layer.objects.active = root
            else:
                notes.append("'%s' collection is excluded from the view layer — tower is hidden" % COLLECTION_NAME)
        finally:
            wm.progress_end()

        gol.last_report = "%d layers  |  %d cells  |  max age %d\nStopped: %s" % (
            len(layers), total, max_age, reason)
        for note in notes:
            gol.last_report += "\n" + note
        self.report({'INFO'}, "GOL: %d generations, %d cells (%s)" % (len(layers), total, reason))
        # Warnings come after the final INFO: the status bar shows only the last
        # report, so otherwise the user would never see them
        for note in notes:
            self.report({'WARNING'}, note)
        return {'FINISHED'}


class GOL_OT_cell(Operator):
    bl_idname = "gol.cell"
    bl_label = "Toggle Cell"
    bl_description = "Toggle this cell between alive and dead"
    # UNDO_GROUPED: a series of brush clicks collapses into one undo step, so
    # Ctrl+Z after closing the editor does not wipe the whole drawing
    bl_options = {'INTERNAL', 'UNDO_GROUPED'}

    index: IntProperty(options={'HIDDEN'})

    def execute(self, context):
        gol = context.scene.gol
        ensure_seed(gol)
        w, h = gol.grid_x, gol.grid_y
        data = list(gol.seed_data)
        i = self.index
        if not (0 <= i < len(data)):
            return {'CANCELLED'}
        new = "0" if data[i] == "1" else "1"
        x, y = i % w, i // w
        xs = {x, w - 1 - x} if gol.sym_x else {x}
        ys = {y, h - 1 - y} if gol.sym_y else {y}
        for yy in ys:
            for xx in xs:
                data[yy * w + xx] = new
        gol.seed_data = "".join(data)
        return {'FINISHED'}


class GOL_OT_seed_edit(Operator):
    bl_idname = "gol.seed_edit"
    bl_label = "Seed Editor"
    bl_description = ("Draw the first generation by clicking cells. "
                      "Opening from Random/Pattern mode captures the current seed for editing "
                      "(Esc/cancel restores the previous drawing)")
    bl_options = {'INTERNAL', 'UNDO'}

    def invoke(self, context, event):
        gol = context.scene.gol
        if gol.grid_x > PAINT_LIMIT or gol.grid_y > PAINT_LIMIT:
            # Checked before any state changes, or the mode would silently
            # switch to PAINT with a useless dialog
            self.report({'ERROR'},
                        "Grid is too large to paint (limit %dx%d) — use Random mode"
                        % (PAINT_LIMIT, PAINT_LIMIT))
            return {'CANCELLED'}
        ensure_seed(gol)
        # Remember the previous state: Esc or cancelling the dialog restores it,
        # so capturing Random/Pattern never destroys a hand-drawn seed
        self._prev_data = gol.seed_data
        self._prev_mode = gol.seed_mode
        if gol.seed_mode != 'PAINT':
            live = seed_cells(gol)
            gol.seed_data = cells_to_string(live, gol.grid_x, gol.grid_y)
            gol.seed_mode = 'PAINT'
        width = min(60 + gol.grid_x * 18, 1000)
        return context.window_manager.invoke_props_dialog(self, width=width)

    def cancel(self, context):
        gol = context.scene.gol
        if hasattr(self, "_prev_data"):
            gol.seed_data = self._prev_data
            gol.seed_mode = self._prev_mode

    def execute(self, context):
        return {'FINISHED'}

    def draw(self, context):
        gol = context.scene.gol
        layout = self.layout
        w, h = gol.grid_x, gol.grid_y
        if w > PAINT_LIMIT or h > PAINT_LIMIT:
            layout.label(
                text="Grid is too large to paint (limit %dx%d) — use Random mode" % (PAINT_LIMIT, PAINT_LIMIT),
                icon='ERROR')
            return
        tools = layout.row(align=True)
        tools.label(text="Alive: %d" % gol.seed_data.count("1"))
        tools.separator()
        tools.prop(gol, "sym_x", text="Sym X", toggle=True)
        tools.prop(gol, "sym_y", text="Sym Y", toggle=True)
        tools.separator()
        tools.operator("gol.seed_fill", text="", icon='X').action = 'CLEAR'
        tools.operator("gol.seed_fill", text="", icon='FILE_REFRESH').action = 'RANDOM'
        tools.operator("gol.seed_fill", text="", icon='ARROW_LEFTRIGHT').action = 'INVERT'
        tools.operator("gol.seed_fill", text="", icon='FRAME_NEXT').action = 'STEP'
        grid = layout.column(align=True)
        data = gol.seed_data
        size = len(data)
        for y in range(h):
            row = grid.row(align=True)
            row.scale_y = 0.85
            base = y * w
            for x in range(w):
                i = base + x
                alive = i < size and data[i] == "1"
                row.operator("gol.cell", text="", depress=alive).index = i


class GOL_OT_seed_fill(Operator):
    bl_idname = "gol.seed_fill"
    bl_label = "Seed Tool"
    bl_options = {'INTERNAL', 'UNDO'}

    action: EnumProperty(
        items=[
            ('CLEAR', "Clear", "Kill every cell"),
            ('RANDOM', "Randomize", "Fill with fresh random cells at the current density"),
            ('INVERT', "Invert", "Swap alive and dead cells"),
            ('STEP', "Step", "Advance the seed one generation using the current rules"),
            ('MIRROR', "Mirror", "Mirror existing cells across the enabled symmetry axes"),
        ],
        options={'HIDDEN'},
    )

    @classmethod
    def description(cls, context, properties):
        return {
            'CLEAR': "Kill every cell",
            'RANDOM': "Fill with fresh random cells at the current density",
            'INVERT': "Swap alive and dead cells",
            'STEP': "Advance the seed one generation using the current rules",
            'MIRROR': "Mirror existing cells across the enabled symmetry axes",
        }.get(properties.action, "")

    def execute(self, context):
        gol = context.scene.gol
        ensure_seed(gol)
        w, h = gol.grid_x, gol.grid_y
        if self.action == 'CLEAR':
            gol.seed_data = "0" * (w * h)
        elif self.action == 'RANDOM':
            gol.seed_data = "".join(
                "1" if pyrandom.random() < gol.density else "0" for _ in range(w * h))
        elif self.action == 'INVERT':
            gol.seed_data = "".join("0" if c == "1" else "1" for c in gol.seed_data)
        elif self.action == 'STEP':
            birth, survive = parse_rules(gol)
            live = string_to_cells(gol.seed_data, w)
            live = gol_step(live, w, h, birth, survive, gol.wrap)
            gol.seed_data = cells_to_string(live, w, h)
        elif self.action == 'MIRROR':
            live = string_to_cells(gol.seed_data, w)
            extra = set()
            for x, y in live:
                if gol.sym_x:
                    extra.add((w - 1 - x, y))
                if gol.sym_y:
                    extra.add((x, h - 1 - y))
                if gol.sym_x and gol.sym_y:
                    extra.add((w - 1 - x, h - 1 - y))
            gol.seed_data = cells_to_string(live | extra, w, h)
        return {'FINISHED'}


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------

class GOL_PT_main(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GOL"
    bl_label = "GOL Automata"

    def draw(self, context):
        gol = context.scene.gol
        layout = self.layout
        row = layout.row()
        row.scale_y = 1.6
        row.operator("gol.generate", text="Generate Tower", icon='PLAY')
        layout.prop(gol, "replace_previous")
        if gol.last_report:
            box = layout.box().column(align=True)
            for line in gol.last_report.split("\n"):
                box.label(text=line)


class GOL_PT_seed(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GOL"
    bl_parent_id = "GOL_PT_main"
    bl_label = "Seed"

    def draw(self, context):
        gol = context.scene.gol
        layout = self.layout

        # Picking the mode is the main decision in this panel, hence at the top
        # and full width (inside use_property_split the buttons get clipped)
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.prop(gol, "seed_mode", expand=True)

        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(align=True)
        col.prop(gol, "grid_x", text="Grid Width")
        col.prop(gol, "grid_y", text="Height")

        if gol.seed_mode == 'RANDOM':
            col = layout.column(align=True)
            col.prop(gol, "density")
            col.prop(gol, "rand_seed")
        elif gol.seed_mode == 'PATTERN':
            layout.prop(gol, "pattern")
        else:  # PAINT
            ensure = gol.seed_data  # read-only inside draw
            alive = ensure.count("1")
            layout.label(text="Alive cells: %d" % alive)
            row = layout.row(align=True)
            row.operator("gol.seed_fill", text="Clear", icon='X').action = 'CLEAR'
            row.operator("gol.seed_fill", text="Invert", icon='ARROW_LEFTRIGHT').action = 'INVERT'
            row.operator("gol.seed_fill", text="Step", icon='FRAME_NEXT').action = 'STEP'

        row = layout.row()
        row.scale_y = 1.3
        paintable = gol.grid_x <= PAINT_LIMIT and gol.grid_y <= PAINT_LIMIT
        row.enabled = paintable
        row.operator("gol.seed_edit", text="Edit Seed", icon='BRUSH_DATA')
        if not paintable:
            layout.label(text="Grid over %dx%d — painting disabled" % (PAINT_LIMIT, PAINT_LIMIT),
                         icon='INFO')


class GOL_PT_rules(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GOL"
    bl_parent_id = "GOL_PT_main"
    bl_label = "Rules"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        gol = context.scene.gol
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(gol, "rule_preset", text="Preset")
        col = layout.column(align=True)
        col.prop(gol, "rule_birth", text="Birth (B)")
        col.prop(gol, "rule_survive", text="Survive (S)")
        layout.prop(gol, "wrap")
        layout.prop(gol, "stop_loop")
        layout.prop(gol, "use_noise")
        if gol.use_noise:
            col = layout.column(align=True)
            col.prop(gol, "noise_amount", text="Amount")
            # The seed also drives the noise pattern, and in Pattern/Paint modes
            # there is nowhere else to expose the field
            col.prop(gol, "rand_seed", text="Seed")


class GOL_PT_tower(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GOL"
    bl_parent_id = "GOL_PT_main"
    bl_label = "Tower"

    def draw(self, context):
        gol = context.scene.gol
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(gol, "generations")
        row = layout.row()
        row.prop(gol, "time_axis", expand=True)
        if gol.time_axis == 'CURVE':
            # An empty field is the only thing blocking Generate, so highlight
            # it rather than staying silent
            sub = layout.column()
            sub.alert = gol.path_object is None
            sub.prop(gol, "path_object")
            col = layout.column(align=True)
            col.prop(gol, "path_fit", text="Spacing")
            col.prop(gol, "path_align")
            col.prop(gol, "path_reverse")
        else:
            row = layout.row()
            row.prop(gol, "direction", expand=True)
        col = layout.column(align=True)
        col.prop(gol, "voxel_size")
        col.prop(gol, "scale_xy")
        col.prop(gol, "scale_z")
        # On a curve the curve itself sets where the tower goes
        row = layout.row()
        row.active = gol.time_axis == 'Z'
        row.prop(gol, "at_cursor")


class GOL_PT_effects(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GOL"
    bl_parent_id = "GOL_PT_main"
    bl_label = "Shape FX"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        gol = context.scene.gol
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column(align=True)
        col.prop(gol, "spread_xy")
        col.prop(gol, "layer_spacing")
        layout.prop(gol, "twist")
        layout.prop(gol, "taper")
        layout.prop(gol, "age_scale")
        layout.prop(gol, "jitter")


class GOL_PT_output(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GOL"
    bl_parent_id = "GOL_PT_main"
    bl_label = "Output"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        gol = context.scene.gol
        layout = self.layout
        # Style switch: a row of icons (like the snapping modes), with the name
        # of the active style underneath
        col = layout.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.35
        row.prop(gol, "style", icon_only=True, expand=True)
        item = gol.bl_rna.properties["style"].enum_items[gol.style]
        name_row = col.row()
        name_row.alignment = 'CENTER'
        name_row.label(text=item.name)
        layout.use_property_split = True
        layout.use_property_decorate = False
        if gol.style == 'OBJECT':
            layout.prop(gol, "instance_object")
        if gol.style == 'WAX':
            layout.prop(gol, "wax_detail")
        layout.prop(gol, "combine")
        if gol.style in {'CUBES', 'PLANES'}:
            layout.prop(gol, "weld")
        layout.prop(gol, "add_attributes")
        if gol.style in {'CUBES', 'PLANES', 'WAX'}:
            layout.prop(gol, "color_by_age")
            if gol.color_by_age:
                layout.prop(gol, "material")


class GOL_PT_animation(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GOL"
    bl_parent_id = "GOL_PT_main"
    bl_label = "Animation"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.prop(context.scene.gol, "animate", text="")

    def draw(self, context):
        gol = context.scene.gol
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.active = gol.animate
        layout.prop(gol, "frame_step", text="Frames per Gen")
        if gol.combine == 'SINGLE':
            layout.label(text="Single mesh: Build modifier", icon='MOD_BUILD')
        else:
            layout.label(text="Per generation: visibility keys", icon='KEYFRAME_HLT')


def menu_add_entry(self, context):
    self.layout.operator("gol.generate", text="GOL Life Tower", icon='MOD_BUILD')


# ----------------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------------

classes = (
    GOLSettings,
    GOL_OT_generate,
    GOL_OT_cell,
    GOL_OT_seed_edit,
    GOL_OT_seed_fill,
    GOL_PT_main,
    GOL_PT_seed,
    GOL_PT_rules,
    GOL_PT_tower,
    GOL_PT_effects,
    GOL_PT_output,
    GOL_PT_animation,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gol = PointerProperty(type=GOLSettings)
    bpy.types.VIEW3D_MT_mesh_add.append(menu_add_entry)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(menu_add_entry)
    del bpy.types.Scene.gol
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
