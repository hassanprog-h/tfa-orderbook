"""``python -m server [--host 127.0.0.1] [--port 8000]``"""

import argparse
import sys

try:
    import uvicorn
except ModuleNotFoundError:
    sys.exit(
        "The web server needs FastAPI and uvicorn, which this Python does not have:\n"
        f"  {sys.executable}\n\n"
        "Use the project's virtual environment:  .venv/bin/python -m server\n"
        'or install them here:                     pip install -e ".[server]"'
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"\n  TFA Order Book running on http://{args.host}:{args.port}  (API docs: /docs)\n")
    uvicorn.run("server.main:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
