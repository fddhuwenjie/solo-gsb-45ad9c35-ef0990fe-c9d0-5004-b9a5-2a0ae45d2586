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


class PreviewChange(BaseModel):
    """一条审查结果发生变化的引用：登记前 / 假定登记后的完整结果。"""

    citation_id: int
    before: CitationAudit
    after: CitationAudit


class PreviewResponse(BaseModel):
    """POST /corpus/preview 的响应：仅列出受拟登记版本影响的存量引用。"""

    doc_id: str
    version: str
    target_at: str
    total_citations: int = Field(
        ..., description="参与比较的库中原有引用总数（不含拟登记版本自带引用）"
    )
    changed: int = Field(
        ..., description="结论 / 解析版本 / 链路发生变化的引用数"
    )
    warnings: list[str] = Field(
        ..., description="拟登记版本的校验警告（与 POST /corpus 相同口径）"
    )
    changes: list[PreviewChange] = Field(
        ..., description="发生变化的引用，按原引用 ID 升序"
    )


class HealthResponse(BaseModel):
    status: str
    database: str
