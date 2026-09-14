import argparse
import asyncio

from kivi.config import settings
from kivi.models.provider import GeminiProvider, ModelProvider, ProviderError, SarvamProvider, get_provider


async def _check_one(label: str, provider: ModelProvider) -> None:
    try:
        completion = await provider.complete(
            [{"role": "user", "content": "Say OK"}],
            tier="extraction",
        )
        print(
            f"[{label}] OK  model={completion.model} "
            f"tokens_in={completion.tokens_in} tokens_out={completion.tokens_out} "
            f"latency_ms={completion.latency_ms} cost_usd=${completion.cost_usd:.6f}"
        )
        print(f"    content: {completion.content!r}")
    except Exception as exc:  # noqa: BLE001 — this is a diagnostic command, report and continue
        print(f"[{label}] FAILED  {exc}")


async def check_models() -> None:
    if settings.sarvam_api_key:
        await _check_one("sarvam", SarvamProvider(settings.sarvam_api_key, settings.sarvam_base_url))
    else:
        print("[sarvam] SKIPPED  SARVAM_API_KEY not set")

    if settings.gemini_api_key:
        await _check_one("gemini", GeminiProvider(settings.gemini_api_key, settings.gemini_base_url))
    else:
        print("[gemini] SKIPPED  GEMINI_API_KEY not set")

    print()
    try:
        active = get_provider()
        print(f"Active provider (fallback selection): {active.name}")
    except ProviderError as exc:
        print(f"No provider available: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="kivi")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check-models", help="Call each configured provider once and report cost")

    args = parser.parse_args()

    if args.command == "check-models":
        asyncio.run(check_models())


if __name__ == "__main__":
    main()
