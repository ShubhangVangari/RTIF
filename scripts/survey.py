import os, json
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
surface = Path("/tmp/corpus_surface.txt").read_text()
prompt = (ROOT / "prompts" / "survey.txt").read_text()

SCHEMA = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "type": {"type": "string", "enum": ["concept", "entity"]},
                    "summary": {"type": "string"},
                    "document_ids": {"type": "array", "items": {"type": "string"}},
                    "academic_count": {"type": "integer"},
                    "practitioner_count": {"type": "integer"},
                    "is_contested": {"type": "boolean"},
                    "contention_notes": {"type": "string"},
                },
                "required": ["slug", "type", "summary", "document_ids",
                             "academic_count", "practitioner_count", "is_contested"],
            },
        }
    },
    "required": ["clusters"],
}

client = genai.Client(
    vertexai=True,
    project=os.environ["GOOGLE_CLOUD_PROJECT"],
    location="us-central1",
)

print("calling gemini-2.5-pro...")
resp = client.models.generate_content(
    model="gemini-2.5-pro",
    contents=f"{prompt}\n\n---\n\nCORPUS:\n\n{surface}",
    config=types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        response_schema=SCHEMA,
    ),
)

data = json.loads(resp.text)
out = ROOT / "wiki_local" / "survey.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(data, indent=2))

u = resp.usage_metadata
print(f"in {u.prompt_token_count:,} / out {u.candidates_token_count:,} tokens")
print(f"{len(data['clusters'])} clusters -> {out}")