"""响应模型（OpenAPI 文档与输出校验）。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class NodeRef(BaseModel):
    doc_id: str
    version: str
    clause_id: str


class SourceRef(BaseModel):
    doc_id: str
    version: str
    clause_id: str


class TargetRef(BaseModel):
    doc_id: str
    clause_id: str
    pinned_version: Optional[str] = None


class RecordedBy(BaseModel):
    doc_id: str
    version: str


class ChainStep(BaseModel):
    node: Optional[NodeRef]
    title: Optional[str] = None
    effective_at: Optional[str] = None
    expires_at: Optional[str] = None
    state_at_target: str
    edge_in: Optional[str] = Field(
        None, description="进入本节点的修订关系：revises / repeals；根节点为 null"
    )
    terminal: bool = False
    note: Optional[str] = None
    recorded_by: Optional[RecordedBy] = None
    exists_in_corpus: bool = True


class Chain(BaseModel):
    anchor: NodeRef
    branch_index: int
    steps: list[ChainStep]


class CitationAudit(BaseModel):
    citation_id: int
    source: SourceRef
    target: TargetRef
    pinned: bool
    context: Optional[str]
    target_at: str
    status: str
    status_label: str
    reason: str
    resolved: Optional[NodeRef]
    effective_candidates: list[NodeRef]
    cycle: Optional[list[NodeRef]]
    anchors: list[NodeRef]
    chains: list[Chain]


class AuditResponse(BaseModel):
    target_at: str
    total: int
    summary: dict[str, int]
    results: list[CitationAudit]


class CorpusResponse(BaseModel):
    doc_id: str
    version: str
    name: Optional[str]
    effective_at: Optional[str]
    created: bool
    counts: dict[str, int]
    warnings: list[str]


class HealthResponse(BaseModel):
    status: str
    database: str
