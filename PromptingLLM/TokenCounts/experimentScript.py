import tiktoken
from pathlib import Path

def get_encoder():
    try:
        return tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        try:
            return tiktoken.get_encoding("o200k_base")
        except Exception:
            return tiktoken.get_encoding("cl100k_base")

ENC = get_encoder()

def count_tokens(text: str) -> int:
    return len(ENC.encode(text))

# === CHANGE THIS to point to one of your generated .txt prompts ===
TEST_FILE = Path("/Volumes/Rachna-HD/GeneratedPromptsClientsExp7/BBC02/BBC02U0Test_prompt.txt")

if not TEST_FILE.exists():
    print(f"File not found: {TEST_FILE}")
else:
    content = TEST_FILE.read_text(encoding="utf-8")
    tokens = count_tokens(content)
    print(f"{TEST_FILE.name}: {tokens} tokens")
