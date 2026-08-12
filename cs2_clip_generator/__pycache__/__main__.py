"""``python -m cs2_clip_generator`` starts the GUI; add ``cli`` for the CLI."""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        from .cli import main as cli_main

        return cli_main(sys.argv[2:])
    from .app.main import main as gui_main

    return gui_main(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
