"""BoarAI — a minimal, hot-pluggable, cross-platform microkernel agent framework.

Top-level distribution package (PyPI: ``boarai``). Layout:

- ``boarai.core``      the microkernel (formerly the top-level ``core``
  package; renamed for PyPI so the generic name ``core`` never lands in
  site-packages where it could collide with other distributions)
- ``boarai.__main__``  the console entry (``python -m boarai`` / the
  ``boarai`` script / the source tree's ``main.py``)

Resource trees (``boar/prompts``, ``boar/defaults``, ``boar/extensions``)
stay top-level next to this package; runtime data lives outside the tree
in the data-dir anchor (default ``~/.boarai``, see
``boarai.core.runtime.data_paths``).

Deliberately side-effect free: importing ``boarai`` must not pull in the
kernel — ``import boarai.core`` does that explicitly.
"""
