"""Deterministic FreeCiv state-to-atoms and action-validation adapter (Issue #6).

Converts the `llm_optimized` game state exposed by the ``taso-ventures/freeciv-llm``
proxy into normalized facts and MeTTa/PLN atoms, and validates candidate actions before
they are submitted to the game. Pure-Python, stdlib-only, deterministic — host-runnable
without the game server, chromadb, or torch.

The module *aligns* to freeciv-llm's documented API and validation semantics (it does not
vendor any of that AGPL-3.0 code). See:
  - freeciv-proxy/state_extractor.py  (llm_optimized shape)
  - freeciv-proxy/action_validator.py (action legality)
  - freeciv-proxy/action_constants.py (action ids)
"""

__all__ = ["schemas", "adapter", "atoms", "actions"]
