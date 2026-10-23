"""Does Anthropic accept the copilot's Claude key? Run on the real-money server by `deploy.py ai-key`, as the
service user with the keys (decision A5: the copilot's AI on real money is Claude, off until this key is set).

One free call - list the models - so nothing is generated and nothing is billed. Prints one line:
    OK: ...           the key works; the copilot uses Claude after the restart
    REFUSED: ...      Anthropic says the key is wrong or has no access (deploy.py removes it again)
    UNREACHABLE: ...  no answer (network, Anthropic down): the key stays, the copilot tries on its own later
    NO KEY            nothing to check
The key is never printed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the release folder: `backend` imports from there

from backend.gateway.config import settings  # noqa: E402


def main() -> None:
    key = settings.copilot_claude_api_key.get_secret_value().strip() if settings.copilot_claude_api_key else ""
    if not key:
        print("NO KEY")
        return
    try:
        import anthropic
    except ImportError:
        print("UNREACHABLE: the anthropic package is missing on this server - release a newer version first")
        return
    # The address is pinned: an ANTHROPIC_BASE_URL in some environment must never redirect the key elsewhere.
    client = anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com", timeout=20, max_retries=1)
    try:
        client.models.list(limit=1)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        print(f"REFUSED: {type(exc).__name__}")
        return
    except anthropic.APIError as exc:
        print(f"UNREACHABLE: {type(exc).__name__}")
        return
    print(f"OK: Anthropic accepted the key; the copilot uses {settings.copilot_claude_model}")


if __name__ == "__main__":
    main()
