from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from kivi.models.provider import Completion, ModelProvider

PROMPT_VERSION = "gate_v1"
_PROMPT = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")

GateReason = Literal[
    "acknowledgement_only",
    "transient_scheduling",
    "restates_known_fact",
    "ambiguous_referent",
    "personal_or_health_content",
    "opinion_about_colleague",
    "no_durable_content",
    "contains_durable_content",
]


class GateDecision(BaseModel):
    proceed: bool
    reason: GateReason
    detail: str


async def run_gate(
    provider: ModelProvider, dictation_text: str, app_context: str | None
) -> tuple[GateDecision, Completion]:
    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": f"App context: {app_context or 'unknown'}\n\nDictation:\n{dictation_text}"},
    ]
    completion = await provider.complete(messages, schema=GateDecision, tier="extraction")
    return GateDecision.model_validate_json(completion.content), completion
