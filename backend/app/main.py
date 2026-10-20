from typing import Optional, Dict, List

from fastapi import FastAPI, HTTPException, Depends, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.app.agents.workflows import build_workflow
from backend.app.agents.tools_registry import TOOLS_REGISTRY
from backend.knowledge.search import search_runbooks
from backend.app.metrics import (
    METRICS_CONTENT_TYPE,
    get_metrics,
    track_incident,
    track_tool,
)
from backend.app.config import settings


app = FastAPI(
    title="Incident & Reliability API",
    description="AI agent for incident analysis, diagnostics, and runbook retrieval",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = build_workflow()


# ---------- Models ----------

class IncidentRequest(BaseModel):
    input: str
    service: Optional[str] = None


class ToolRequest(BaseModel):
    args: Dict = {}


class RunbookSearchResponse(BaseModel):
    query: str
    results: List[Dict]


# ---------- Security (API key disabled but structure preserved) ----------

SAFE_TOOLS = {
    "check_pod_health",
    "get_logs",
    "get_pod_logs",
    "rag_retrieval",
    "restart_service",
    "scale_service",
    "invalidate_cache",
    "terminate_idle_db_sessions",
    "simulate_incident",
}


def require_api_key(x_api_key: str | None = Header(None)):
    return True


# ---------- Endpoints ----------

@app.post("/incident")
def investigate_incident(payload: IncidentRequest):
    print("STATE SENT TO WORKFLOW:", {
        "input": payload.input,
        "service": payload.service
    })

    agent = build_workflow()
    print("TICK START")

    try:
        result = agent.invoke({
            "input": payload.input,
            "service": payload.service
        })
        print("RESULT:", result)
        return result
    except Exception as e:
        print("AGENT ERROR:", type(e).__name__, e)
        raise


# 2) Runbook search endpoint (real pgvector search)
@app.get("/runbooks", response_model=RunbookSearchResponse)
def search_runbooks_endpoint(query: str):
    results = search_runbooks(query)
    return RunbookSearchResponse(query=query, results=results)


# 3) Tools endpoint (secure structure, but API key disabled)
@app.post("/tools/{tool_name}", dependencies=[Depends(require_api_key)])
def execute_tool(tool_name: str, payload: ToolRequest):
    if tool_name not in SAFE_TOOLS:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not allowed")

    if tool_name not in TOOLS_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not available")

    fn = TOOLS_REGISTRY[tool_name]

    try:
        with track_tool(tool_name):
            result = fn(**payload.args)
    except TypeError as e:
        raise HTTPException(status_code=400, detail=f"Bad arguments for tool '{tool_name}': {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tool '{tool_name}' failed: {e}")

    return {"tool": tool_name, "result": result}


# 4) Health endpoint
@app.get("/health")
def health_check():
    return {"status": "ok"}


# 5) Metrics endpoint (real Prometheus exporter)
@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(get_metrics(), media_type=METRICS_CONTENT_TYPE)
