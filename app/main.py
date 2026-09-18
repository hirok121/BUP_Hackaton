from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI()

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=500,
                detail="OPENAI_API_KEY is not set in the environment",
            )
        _client = OpenAI(api_key=api_key)
    return _client


class ChatRequest(BaseModel):
    prompt: str


class ChatResponse(BaseModel):
    response: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    completion = get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": request.prompt}],
    )
    return ChatResponse(response=completion.choices[0].message.content)


@app.post("/optimize-energy")
def optimize_energy():
    pass
