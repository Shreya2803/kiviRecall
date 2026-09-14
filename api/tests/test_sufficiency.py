import json

import pytest

from kivi.db.enums import MemoryStatus, MemoryType, SufficiencyVerdict
from kivi.db.models import Memory
from kivi.models.provider import Completion
from kivi.retrieval.fusion import FusedCandidate
from kivi.retrieval.sufficiency import run_sufficiency


class FakeProvider:
    name = "fake"

    def __init__(self, response_json: dict):
        self.response_json = response_json
        self.called = False

    async def complete(self, messages, *, schema=None, tier):
        self.called = True
        return Completion(
            content=json.dumps(self.response_json), tokens_in=1, tokens_out=1,
            model="fake", latency_ms=0, cost_usd=0.0,
        )


def _fc(memory_id: int, *, rrf_score: float, confidence: float, claim: str = "Tara launches March 20") -> FusedCandidate:
    memory = Memory(
        id=memory_id, memory_type=MemoryType.PROJECT_STATE, claim=claim, claim_hash="h",
        status=MemoryStatus.ACTIVE, extraction_run_id=1,
    )
    return FusedCandidate(
        memory_id=memory_id, memory=memory, rrf_score=rrf_score, boosted_score=rrf_score,
        confidence=confidence,
    )


@pytest.mark.asyncio
async def test_unresolved_entity_forces_insufficient_without_calling_the_model() -> None:
    provider = FakeProvider({"verdict": "sufficient", "reason": "should never be read"})

    result, completions = await run_sufficiency(
        provider, "What did I decide about the Hyderabad office move?", [], ["Hyderabad office"],
    )

    assert result.verdict == SufficiencyVerdict.INSUFFICIENT
    assert "Hyderabad office" in result.reason
    assert completions == []
    assert provider.called is False


@pytest.mark.asyncio
async def test_no_candidates_forces_insufficient_without_calling_the_model() -> None:
    provider = FakeProvider({"verdict": "sufficient", "reason": "should never be read"})

    result, completions = await run_sufficiency(provider, "When does Tara launch?", [], [])

    assert result.verdict == SufficiencyVerdict.INSUFFICIENT
    assert completions == []
    assert provider.called is False


@pytest.mark.asyncio
async def test_fused_score_below_floor_forces_insufficient() -> None:
    provider = FakeProvider({"verdict": "sufficient", "reason": "should never be read"})
    weak = _fc(1, rrf_score=0.0001, confidence=1.0)

    result, completions = await run_sufficiency(provider, "When does Tara launch?", [weak], [])

    assert result.verdict == SufficiencyVerdict.INSUFFICIENT
    assert "fused score" in result.reason
    assert provider.called is False


@pytest.mark.asyncio
async def test_single_low_confidence_memory_forces_insufficient() -> None:
    provider = FakeProvider({"verdict": "sufficient", "reason": "should never be read"})
    unsure = _fc(1, rrf_score=0.5, confidence=0.3)

    result, completions = await run_sufficiency(provider, "When does Tara launch?", [unsure], [])

    assert result.verdict == SufficiencyVerdict.INSUFFICIENT
    assert "one supporting memory" in result.reason
    assert provider.called is False


@pytest.mark.asyncio
async def test_no_deterministic_rule_fires_defers_to_the_model() -> None:
    provider = FakeProvider({"verdict": "partial", "reason": "names the owner but not the date"})
    strong_a = _fc(1, rrf_score=0.5, confidence=1.0)
    strong_b = _fc(2, rrf_score=0.4, confidence=1.0)

    result, completions = await run_sufficiency(provider, "When does Tara launch?", [strong_a, strong_b], [])

    assert provider.called is True
    assert result.verdict == SufficiencyVerdict.PARTIAL
    assert result.reason == "names the owner but not the date"
    assert len(completions) == 1
