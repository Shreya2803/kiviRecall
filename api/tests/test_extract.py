import json

import pytest

from kivi.memory.extract import run_extraction
from kivi.models.provider import Completion


class FakeProvider:
    """Returns a fixed wire-level JSON payload without any network call."""

    name = "fake"

    def __init__(self, wire_json: dict):
        self.wire_json = wire_json

    async def complete(self, messages, *, schema=None, tier):
        return Completion(
            content=json.dumps(self.wire_json), tokens_in=1, tokens_out=1,
            model="fake", latency_ms=0, cost_usd=0.0,
        )


def _wire_claim(text: str, category: str = "decision", entities: list[dict] | None = None) -> dict:
    return {
        "text": text, "category": category, "source_span": text,
        "char_start": 0, "char_end": len(text), "entities": entities or [],
    }


@pytest.mark.asyncio
async def test_bare_pronoun_with_no_person_entity_is_dropped() -> None:
    text = "They are waiting on a response"
    provider = FakeProvider({"claims": [_wire_claim(text)], "reason": "one claim"})

    result, _ = await run_extraction(provider, text)

    assert result.claims == []


@pytest.mark.asyncio
async def test_pronoun_resolved_by_a_named_person_entity_is_kept() -> None:
    text = "Rohit will send his update tomorrow"
    provider = FakeProvider({
        "claims": [_wire_claim(text, entities=[{"surface_form": "Rohit", "entity_type": "person"}])],
        "reason": "one claim",
    })

    result, _ = await run_extraction(provider, text)

    assert len(result.claims) == 1
    assert result.claims[0].text == text


@pytest.mark.asyncio
async def test_hinglish_unresolved_referent_is_dropped() -> None:
    text = "unki taraf se koi update nahi aya abhi tak"
    provider = FakeProvider({"claims": [_wire_claim(text)], "reason": "one claim"})

    result, _ = await run_extraction(provider, text)

    assert result.claims == []


@pytest.mark.asyncio
async def test_near_miss_category_is_normalized_not_dropped() -> None:
    text = "Kavach is the codename the team uses for the payment gateway project"
    provider = FakeProvider({
        "claims": [_wire_claim(text, category="term")],
        "reason": "one claim",
    })

    result, _ = await run_extraction(provider, text)

    assert len(result.claims) == 1
    assert result.claims[0].category == "terminology"


@pytest.mark.asyncio
async def test_unrecognized_category_drops_only_that_claim() -> None:
    good_text = "Rohit owns Kavach"
    bad_text = "some vendor thing"
    provider = FakeProvider({
        "claims": [
            _wire_claim(bad_text, category="vendor"),
            _wire_claim(good_text, entities=[{"surface_form": "Rohit", "entity_type": "person"}]),
        ],
        "reason": "two claims",
    })

    result, _ = await run_extraction(provider, f"{bad_text}. {good_text}")

    assert len(result.claims) == 1
    assert result.claims[0].text == good_text
