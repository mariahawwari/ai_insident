from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class RootCause(BaseModel):
    hypothesis: Optional[str] = None
    confidence: Optional[str] = None   # "low" | "medium" | "high"
    category: Optional[str] = None
    evidence: List[str] = Field(default_factory=list)
    next_checks: List[str] = Field(default_factory=list)

class ActionRequest(BaseModel):
    action: str   # "restart" | "scale" | "terminate_idle_db_sessions" | "invalidate_cache"
    params: Dict[str, Any] = Field(default_factory=dict)
    reason: str

class IncidentState(BaseModel):
    # -----------------------------
    # INPUT (from API)
    # -----------------------------
    input: Optional[str] = None
    service: Optional[str] = None

    # Intent classification
    intent: Optional[str] = None

    # -----------------------------
    # SIGNALS
    # -----------------------------
    metrics: Dict[str, Any] = Field(default_factory=dict)
    logs: List[str] = Field(default_factory=list)
    database_results: List[Dict[str, Any]] = Field(default_factory=list)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    retrieved_docs: List[Dict[str, Any]] = Field(default_factory=list)

    # -----------------------------
    # REASONING
    # -----------------------------
    correlation_signals: List[str] = Field(default_factory=list)
    root_cause: Optional[RootCause] = None

    # -----------------------------
    # ACTION LOOP
    # -----------------------------
    proposed_action: Optional[ActionRequest] = None
    approved: Optional[bool] = None
    approval_info: Optional[Dict[str, Any]] = None
    escalation_status: Optional[str] = None

    action_results: List[Dict[str, Any]] = Field(default_factory=list)
    verification: Optional[Dict[str, Any]] = None
    attempts: int = 0

    # -----------------------------
    # OUTPUT
    # -----------------------------
    incident_report: Dict[str, Any] = Field(default_factory=dict)
    observations: List[str] = Field(default_factory=list)
