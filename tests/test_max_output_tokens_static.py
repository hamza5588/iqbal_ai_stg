"""
Dependency-free regression test for bug #27 (max output-token config inconsistency).
See tests/test_max_output_tokens.py for the behavioral version.

Run: python tests/test_max_output_tokens_static.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_SRC = (ROOT / "app" / "models" / "models.py").read_text(encoding="utf-8")
CHATBOT_SERVICE_SRC = (ROOT / "app" / "services" / "chatbot_service.py").read_text(encoding="utf-8")


def test_chat_model_does_not_hardcode_max_tokens():
    assert "max_tokens=1024" not in MODELS_SRC, (
        "ChatModel.chat_model must not hardcode max_tokens=1024 - it silently bypassed "
        "Config.OPENAI_MAX_TOKENS/GROQ_MAX_TOKENS and could truncate replies regardless "
        "of the real model's capacity"
    )


def test_chatbot_service_does_not_hardcode_max_tokens():
    assert "max_tokens=1024" not in CHATBOT_SERVICE_SRC


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items()) if name.startswith("test_")]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
    print()
    if failed:
        print(f"{len(failed)}/{len(tests)} test(s) FAILED")
        sys.exit(1)
    print(f"All {len(tests)} tests passed - bug #27 fix is in place.")
    sys.exit(0)
