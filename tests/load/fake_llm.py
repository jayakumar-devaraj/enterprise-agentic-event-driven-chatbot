"""Benchmark-only OpenAI-compatible server: a fixed wait before the first token, then a fixed pace.

Stands in for a remote model so the worker spends its time waiting on I/O, not CPU.
"""

import asyncio
import json
import os
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

FIRST_S = float(os.environ.get("FIRST_S", "1.0"))
TOKEN_S = float(os.environ.get("TOKEN_S", "0.05"))
WORDS = "I understand you were charged twice. A human will review it and contact you soon.".split()
app = FastAPI()


def _chunk(text: str | None, finish: str | None = None) -> str:
    body = {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "fake",
        "choices": [
            {"index": 0, "delta": {"content": text} if text else {}, "finish_reason": finish}
        ],
    }
    return f"data: {json.dumps(body)}\n\n"


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    await asyncio.sleep(FIRST_S)
    if not body.get("stream"):
        return JSONResponse(
            {
                "id": "x",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "fake",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": " ".join(WORDS)},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    async def gen():
        for i, w in enumerate(WORDS):
            yield _chunk(w if i == 0 else " " + w)
            await asyncio.sleep(TOKEN_S)
        yield _chunk(None, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
