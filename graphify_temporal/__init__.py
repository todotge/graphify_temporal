"""graphify-temporal: temporal enrichment for graphify knowledge graphs.

One job: stamp every node in graphify-out/graph.json with file_mtime and add
deterministic preceded_by edges ordered by filesystem timestamp and line number.
Zero LLM cost — pure stat + JSON mutation.
"""

__version__ = "1.0.0"
