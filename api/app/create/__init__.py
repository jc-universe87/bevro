"""Create: "I want something new."

    intent (plain words) → AgentSpec → a builder writes a project →
    Bevro's ordinary discovery finds how to run it → Provider

Connect starts from something that exists; Create starts from a sentence. They
converge at discovery: from there on a created agent is an ordinary provider
with ordinary runtimes, and nothing downstream knows the difference.
"""

from app.create.spec import AgentSpec, Permission
from app.create.specmodel import SpecModelError, get_spec_model

__all__ = ["AgentSpec", "Permission", "SpecModelError", "get_spec_model"]
