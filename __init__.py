# SPDX-FileCopyrightText: 2026 Sorption
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Entry point for the Blender extension format (4.2+).

An extension is loaded as a Python package, which is why __init__.py is
needed. All the logic still lives in gol_automata.py - the very same file
also installs the old way, through Install from Disk, on Blender 3.0+.
"""

from . import gol_automata


def register():
    gol_automata.register()


def unregister():
    gol_automata.unregister()
