"""Run the server: python -m server [--host 0.0.0.0] [--port 8000]."""

import argparse

import uvicorn

from server.app import create_app

parser = argparse.ArgumentParser(prog="server")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8000)
args = parser.parse_args()
uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True)
