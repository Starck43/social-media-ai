"""Legacy compatibility shim for the old `cli/scheduler` debug entrypoint.

The scheduler/debug collection logic now lives in `cli/commands/collect.py`
and is exposed through the main CLI as `python -m cli.main collect run ...`.

This module only forwards `run` to that command so existing invocations like
`python -m cli.scheduler run --source-id 1 --verbose` keep working. No
collection logic lives here anymore — do not extend it.
"""

import sys


def _forward():
    from .main import app

    rest = sys.argv[1:]
    if rest and rest[0] == "run":
        rest = rest[1:]
    app(args=["collect", "run", *rest], standalone_mode=False)


if __name__ == "__main__":
    _forward()
