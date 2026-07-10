"""Fire-and-forget notifications on state transitions: stdout always, Slack if configured."""
from __future__ import annotations

import json
import urllib.request


def notify(cfg, task, event, detail="") -> None:
    line = f"[concierge] {task['id']} → {event}" + (f": {detail}" if detail else "")
    print(line, flush=True)
    url = cfg.get("slack_webhook")
    if url and "slack" in task.get("notify", []):
        body = json.dumps({"text": f"{line}\n{task['title']}"}).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except OSError as e:
            print(f"[concierge] slack notify failed: {e}", flush=True)
