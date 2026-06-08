"""Arranca o TurfWar localmente e expõe-o via ngrok.

Uso:
    python start.py
    python start.py --port 8000
    python start.py --token SEU_NGROK_TOKEN   (ou variável de ambiente NGROK_AUTHTOKEN)
"""
from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="TurfWar + ngrok launcher")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--token", default=os.getenv("NGROK_AUTHTOKEN"), help="ngrok authtoken")
    args = parser.parse_args()

    # Importações tardias para mensagens de erro claras
    try:
        import uvicorn
    except ImportError:
        sys.exit("uvicorn não instalado — corre:  pip install -r server/requirements.txt")

    try:
        from pyngrok import ngrok
    except ImportError:
        sys.exit("pyngrok não instalado — corre:  pip install pyngrok")

    if args.token:
        ngrok.set_auth_token(args.token)

    # Abre o túnel antes de arrancar o servidor
    try:
        tunnel = ngrok.connect(args.port, "http")
        public_url: str = tunnel.public_url  # ex: https://xxxx.ngrok-free.app
        ws_url = public_url.replace("https://", "wss://").replace("http://", "ws://")
        sep = "=" * 54
        print(f"\n{sep}")
        print(f"  URL público :  {public_url}")
        print(f"  WebSocket   :  {ws_url}/ws")
        print(f"  Local       :  http://localhost:{args.port}")
        print(f"{sep}")
        print("  Partilha o URL público com os outros jogadores.")
        print("  Ctrl+C para parar.\n")
    except Exception as exc:
        print(f"[ngrok] falhou ({exc})")
        print(f"  Sem túnel — só acessível em localhost:{args.port}\n")

    uvicorn.run(
        "server.main:app",
        host="127.0.0.1",
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
