"""Local presentation: the HTTP surface, the operator pages and the launcher.

Nothing here decides a schedule. A route translates a request into an application call
and a page renders what comes back, because a domain rule repeated in a handler is a
rule that will eventually disagree with the domain.
"""

from __future__ import annotations

from .api import create_app

__all__ = ["create_app"]
