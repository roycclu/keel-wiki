from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl
from typing import Literal
from uuid import UUID

class TargetPage(BaseModel):
    title: str
    url: str
    wikitext: str
    base_revid: int


class CitationTargetSelection(BaseModel):
    selected_index: int
    explanation: str


class CitationTarget(BaseModel):
    """A citation market and the local context needed to replace it."""

    title: str
    original_template: str
    context: str
    marker: str
    marker_position: int
    marked_wikitext: str


class WebSearchEvidence(BaseModel):
    """A candidate source returned by web search"""

    title: str = Field(description="Title of the candidate webpage")
    url: HttpUrl = Field(description="Canonical URL of the webpage")
    description: str = Field(description="Readable description of the page content")
    extra_snippets: list[str] = Field(description="Readable snippets from page text")


class DecisionCitationSupport(BaseModel):
    supports_claim: bool
    evidence_index: int | None
    explanation: str


class PreparedCitationEdit(BaseModel):
    original_wikitext: str
    new_wikitext: str
    citation: str


class CitationSubmissionResult(BaseModel):
    production: bool
    success: bool = False
    revision_id: int | None = None
    revision_url: str | None = None

class WorkflowRunResult(BaseModel):
    run_id: UUID
    started_at: datetime
    finished_at: datetime | None = None
    success: bool
    status: Literal["SUCCESS", "FAILURE"]
    failed_stage: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    citation_submission_result: CitationSubmissionResult | None = None

