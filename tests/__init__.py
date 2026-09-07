"""Makes ``tests`` a package so every test module gets a unique dotted name.

Required once ``tests/scenario_platform/`` (a subpackage) exists: without
this file, pytest's default "prepend" import mode registers a subdirectory
containing ``__init__.py`` under its own *bare* directory name in
``sys.modules`` -- so ``tests/scenario_platform/`` would register itself as
top-level ``scenario_platform``, colliding with (and silently replacing in
``sys.modules``) the real ``scenario_platform`` package this whole test
suite imports from ``src/``. An earlier attempt at this subpackage, named
``tests/platform/``, hit the identical failure mode against the *stdlib*
``platform`` module instead. Namespacing every test module under ``tests.*``
removes the collision at its root rather than by renaming around the next
name that happens to already be taken.
"""
