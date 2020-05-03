# Parts copyright 2019-present MagicStack Inc. and the EdgeDB authors.
# Borrowed from the EdgeDB project.  Licensed under Apache License,
# Version 2.0.

"""A fake flake8 plugin to teach pyflakes the `from typing import *` idiom.

The purpose of this module is to act as if it is a valid flake8 plugin.

flake8 then can happily import it, which enables us to monkey-patch the
`pyflakes.checker.Checker` class to extend builtins with stuff from the
`typing` module (if it was imported with `from typing import *`).
"""


import re
import typing

from pyflakes import checker, messages


typing_star_import_re = re.compile(
    r"""^ (?: from \s* typing \s* import \s* \* ) (?:\s*) (?:\#[^\n]*)? $""",
    re.VERBOSE | re.MULTILINE,
)


# Remember the old pyflakes.Checker.__init__
old_init = checker.Checker.__init__
old_report = checker.Checker.report


def __init__(self, tree, filename="(none)", builtins=None, *args, **kwargs):
    try:
        with open(filename, "rt") as f:
            source = f.read()
    except FileNotFoundError:
        pass
    else:
        typing_all = set(typing.__all__)
        # Add any names missing in `typing.__all__`.
        typing_all.add("Protocol")  # added: 3.8.0
        if typing_star_import_re.search(source):
            if builtins:
                builtins = set(builtins) | typing_all
            else:
                builtins = typing_all

    old_init(self, tree, filename, builtins, *args, **kwargs)


def report(self, messageClass, *args, **kwargs):
    if messageClass is messages.ImportStarUsed:
        if not kwargs and len(args) == 2:
            node, module = args
            if module == "typing":
                return

    if messageClass is messages.UnusedImport:
        if not kwargs and len(args) == 2:
            source, value = args
            if value == "typing.*":
                return

    old_report(self, messageClass, *args, **kwargs)


# Monkey-patch pyflakes.Checker.__init__
checker.Checker.__init__ = __init__
checker.Checker.report = report


class MonkeyPatchPyFlakesChecker:

    name = "monkey-patch-pyflakes"
    version = "0.0.1"

    def __init__(self, tree, filename):
        pass

    def run(self):
        return iter(())
