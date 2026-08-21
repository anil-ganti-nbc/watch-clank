"""Supported fail-closed Watch Clank dashboard launcher."""

from __future__ import annotations

import argparse

import uvicorn

from app.core.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    settings = Settings(app_host=args.host, app_port=args.port)
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    main()
