"""Blocking PostgreSQL and Neo4j access, one module per aggregate.

Every query is a line-by-line port of the profile service's async repositories;
parameters, ordering and the optimistic write conditions are kept identical so
the generated text and the write race semantics do not drift.

Import each repository from its own module; this package re-exports nothing so a
reader can tell which aggregate a query belongs to from the import alone.
"""
