"""Dispatcher building blocks (ADR-014), reached as ``scripts/hpw dispatcher ...``.

Block 1, the verified GitHub write, lives in ``write.py``; ``gateway.py`` is the
one place that runs ``gh``. The bash dispatcher keeps the control flow and calls
these blocks through thin shims.
"""
