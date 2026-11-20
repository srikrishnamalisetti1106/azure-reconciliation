"""
FastAPI front door for the assistant.

Run from the repo root:
    uvicorn api.main:app --reload
Then open http://127.0.0.1:8000/docs to try the endpoints in your browser.
"""

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant import tools  # noqa: E402
from assistant.agent import ask, build_graph  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.graph = await build_graph()  # build the agent once at startup
    yield


app = FastAPI(title="Reconciliation Investigation API", lifespan=lifespan)


class Question(BaseModel):
    question: str = Field(..., max_length=1000, examples=["Why is PAY-000003 unmatched?"])


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/exceptions")
def exceptions(reason_code: str = "", min_exposure_cents: int = 0):
    return json.loads(tools.list_exceptions(reason_code, min_exposure_cents))


@app.post("/api/chat/investigate")
async def investigate(q: Question):
    return await ask(app.state.graph, q.question)
