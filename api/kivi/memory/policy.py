from dataclasses import dataclass

from kivi.memory.extract import KEEP_CATEGORIES, ExtractedClaim


@dataclass(frozen=True)
class PolicyVerdict:
    keep: bool
    rejected_category: str | None = None


def apply_policy(claim: ExtractedClaim) -> PolicyVerdict:
    if claim.category in KEEP_CATEGORIES:
        return PolicyVerdict(keep=True)
    return PolicyVerdict(keep=False, rejected_category=claim.category)
