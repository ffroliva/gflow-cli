"""High-level orchestration services (saga layer).

Each module in this package owns one multi-step business transaction and
is responsible for persist-before-spend semantics, crash recovery, and
structured observability.
"""
