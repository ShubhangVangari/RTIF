import os
import uvicorn
from fastapi import FastAPI
from google.cloud import storage
from google import genai

app = FastAPI()

BUCKET = os.environ["BUCKET"]
PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/smoke")
def smoke():
    result = {}

    client = storage.Client()
    blob = client.bucket(BUCKET).blob("smoke/test.txt")
    blob.upload_from_string("joints connected")
    result["storage"] = blob.download_as_text()

    gc = genai.Client(vertexai=True, project=PROJECT, location="us-central1")
    resp = gc.models.generate_content(
        model="gemini-2.5-flash",
        contents="Reply with exactly: vertex ok",
    )
    result["vertex"] = resp.text.strip()

    return result

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))