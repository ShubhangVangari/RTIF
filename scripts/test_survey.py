# from google import genai
# import os
# from dotenv import load_dotenv
# load_dotenv()          # reads .env into os.environ

# client = genai.Client(
#     vertexai=True,
#     project=os.environ["GOOGLE_CLOUD_PROJECT"],
#     location="us-central1",
# )

# response = client.models.generate_content(
#     model="gemini-2.5-pro",
#     contents="Say hello in exactly three words.",
# )

# print(response.text)


import os
from pathlib import Path
from dotenv import load_dotenv
from google import genai

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
SURFACE = Path("/tmp/corpus_surface.txt")

surface = SURFACE.read_text()
lines = [l for l in surface.splitlines() if l.startswith("[DOC-")]

print(f"characters: {len(surface):,}")
print(f"documents:  {len(lines)}")
print(f"first:      {lines[0]}")
print(f"last:       {lines[-1]}")

client = genai.Client(
    vertexai=True,
    project=os.environ["GOOGLE_CLOUD_PROJECT"],
    location="us-central1",
)

tokens = client.models.count_tokens(
    model="gemini-2.5-pro",
    contents=surface,
)
print(f"tokens:     {tokens.total_tokens:,}")