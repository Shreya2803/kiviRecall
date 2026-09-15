import pytest

from kivi.memory.extract import ExtractedClaim
from kivi.memory.policy import apply_policy

KEEP_CATEGORIES = ["project_state", "role", "decision", "commitment", "terminology", "preference"]
REJECT_CATEGORIES = [
    "mood_emotional", "health_personal", "opinion_about_colleague",
    "financial_or_family", "ambiguous_or_unclear",
]


def _claim(category: str) -> ExtractedClaim:
    return ExtractedClaim(
        text="irrelevant", category=category, source_span="irrelevant",
        char_start=0, char_end=10, entities=[],
    )


@pytest.mark.parametrize("category", KEEP_CATEGORIES)
def test_keep_categories_are_kept(category: str) -> None:
    verdict = apply_policy(_claim(category))
    assert verdict.keep is True
    assert verdict.rejected_category is None


@pytest.mark.parametrize("category", REJECT_CATEGORIES)
def test_every_excluded_category_is_rejected(category: str) -> None:
    verdict = apply_policy(_claim(category))
    assert verdict.keep is False
    assert verdict.rejected_category == category
