#!/usr/bin/env python3
"""Static checks over every script in scripts/.

WHY THIS EXISTS
---------------
A sed meant to remove a duplicated `import sys` deleted `import socket` from
udp_audio_record.py instead. The file still parsed, so `ast.parse` was happy;
the recorder died at startup with

    NameError: name 'socket' is not defined

and a 30-second session captured nothing. It was caught only by reading the
session log on a hunch, because a zero-call result is indistinguishable from a
quiet band.

`test_no_undefined_names` catches exactly that class in milliseconds.

Importing the modules instead is not an option for all of them:
udp_audio_record.py has no `if __name__ == '__main__'` guard and executes its
recording loop at module level, so importing it would bind UDP sockets and
start writing files. The static check works regardless.
"""
from __future__ import annotations

import ast
import builtins
import os
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules that are pure definitions and therefore safe to actually import.
IMPORTABLE = ['sdr_db', 'op25_log']


def bound_names(tree: ast.AST) -> set[str]:
    """Every name bound anywhere in the module.

    Deliberately flat rather than scope-aware: a real scope analysis would find
    more, but would also produce false positives that get suppressed and then
    stop being read. This finds "never bound anywhere", which is the failure
    that actually happened, with no false positives.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            args = getattr(node, 'args', None)
            if args:
                for a in (list(args.args) + list(args.posonlyargs)
                          + list(args.kwonlyargs)):
                    names.add(a.arg)
                if args.vararg:
                    names.add(args.vararg.arg)
                if args.kwarg:
                    names.add(args.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Global):
            names.update(node.names)
        elif isinstance(node, (ast.Lambda,)):
            for a in list(node.args.args) + list(node.args.kwonlyargs):
                names.add(a.arg)
    return names


def used_names(tree: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def read(path: str) -> str:
    with open(path, errors='replace') as f:
        return f.read()


def script_paths() -> list[str]:
    return sorted(
        os.path.join(SCRIPTS, f)
        for f in os.listdir(SCRIPTS)
        if f.endswith('.py')
    )


class TestStatic(unittest.TestCase):
    def test_all_scripts_parse(self):
        for path in script_paths():
            with self.subTest(script=os.path.basename(path)):
                ast.parse(read(path), filename=path)

    def test_no_undefined_names(self):
        """The socket-bug check: every name used is bound somewhere."""
        builtin_names = set(dir(builtins)) | {'__file__', '__name__', '__doc__'}
        for path in script_paths():
            name = os.path.basename(path)
            with self.subTest(script=name):
                tree = ast.parse(read(path), filename=path)
                undefined = used_names(tree) - bound_names(tree) - builtin_names
                self.assertEqual(
                    undefined, set(),
                    f'{name} uses names that are never imported or assigned: '
                    f'{sorted(undefined)}. This is the failure mode that made '
                    f'udp_audio_record.py die with NameError while still parsing.',
                )

    def test_pure_modules_import_cleanly(self):
        """sdr_db has no side effects, so a real import is the stronger check."""
        import importlib
        import sys
        sys.path.insert(0, SCRIPTS)
        for mod in IMPORTABLE:
            with self.subTest(module=mod):
                importlib.import_module(mod)

    def test_recorder_has_no_import_time_side_effects_we_forgot_about(self):
        """Document that udp_audio_record.py cannot be imported.

        If someone later adds a `__main__` guard, this test fails and tells them
        to move the module into IMPORTABLE and get the stronger check.
        """
        src = read(os.path.join(SCRIPTS, 'udp_audio_record.py'))
        self.assertNotIn(
            "__name__ == '__main__'", src,
            'udp_audio_record.py now has a main guard, so it can be imported '
            'directly — add it to IMPORTABLE above for a stronger check than '
            'the static one.',
        )


if __name__ == '__main__':
    unittest.main()
