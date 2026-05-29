"""`python -m fbbench` -> the fb-bench CLI."""
import sys

from fbbench.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
