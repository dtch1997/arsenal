"""Serve a stagehand runs/ directory over a Cloudflare quick tunnel.

    uv run python examples/serve.py [runs_dir]    # default: runs

Prints the public status.html URL and blocks until Ctrl-C, then tears the tunnel
down. Point it at the same runs/ dir a sweep is writing to and watch it live — the
dashboard's <meta refresh> updates the page on its own. Requires `cloudflared` on PATH.
"""
import sys
import time

from stagehand import serve


def main():
    runs_dir = sys.argv[1] if len(sys.argv) > 1 else "runs"
    url, stop = serve(runs_dir)
    print(f"dashboard live at: {url}")
    print("Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop()
        print("\nstopped.")


if __name__ == "__main__":
    main()
