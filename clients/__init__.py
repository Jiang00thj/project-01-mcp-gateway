"""Client-side adapters: how an agent runtime consumes this gateway."""

from .openai_tools import (  # noqa: F401
    OpenAIModel, ScriptedModel, estimate_cost, export_tools, run_loop,
)
