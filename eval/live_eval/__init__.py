"""Live eval harness: runs Scout's runtime agents against real (free) models and scores them.

See docs/OPERATIONS.md ("Live model eval") and docs/prompt-findings/README.md. Offline tests drive the same code
with ``scripted.ScriptedLLM``; nothing here needs a network unless a real backend is built in ``backends``.
"""
