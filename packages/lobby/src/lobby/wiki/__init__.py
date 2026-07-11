"""lobby.wiki: a persistent public wiki for reports, hosted on a RunPod CPU pod.

The hub (lobby.daemon) is ephemeral by design — one quick-tunnel URL per
daemon lifetime, apps that die with their processes. The wiki is the opposite:
a named always-on server whose content is a plain file tree you pull, edit,
and push back::

    from lobby import wiki

    w = await wiki.server()                 # find-or-create (default name "wiki")
    tree = await w.pull()                   # whole tree -> ~/.lobby/wiki/<name>/
    (tree / "report.md").write_text("# hi")
    await w.push()                          # tree -> server, atomic
    await w.add("results-site/")            # sugar: pull + copy in + push

See client.py (Wiki handle + server()) and provision.py (RunPod pod plumbing).
"""

from .client import DEFAULT_NAME, Wiki, server

__all__ = ["server", "Wiki", "DEFAULT_NAME"]
