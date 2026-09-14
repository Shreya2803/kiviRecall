from dataclasses import dataclass

from kivi.memory.extract import KEEP_CATEGORIES, ExtractedClaim


@dataclass(frozen=True)
class PolicyVerdict:
    keep: bool
    rejected_category: str | None = None


def apply_policy(claim: ExtractedClaim) -> PolicyVerdict:
    """CLAUDE.md §2's retention boundary, enforced here in code against the
    parsed `category` field — never inferred from the prompt alone. The caller
    must log only `rejected_category`, never the claim text, on rejection."""
    if claim.category in KEEP_CATEGORIES:
        return PolicyVerdict(keep=True)
    return PolicyVerdict(keep=False, rejected_category=claim.category)
