from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ValidationStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class Requirement(BaseModel):
    """One extracted requirement. original_text is verbatim from the source; never edited."""

    framework: str
    version: str
    control_id: str = Field(description="Normalized ID. ECC: 1-1-1 / 1-5-3-1. SAMA: 3.1.2-1 (derived)")
    printed_id: str = Field(description="ID exactly as printed in the document")
    id_is_derived: bool = False
    kind: str = Field(description="control | sub_control | control_consideration")
    level: int
    parent_control_id: str | None = None
    domain: str | None = None
    subdomain_id: str | None = None
    subdomain_title: str | None = None
    principle: str | None = None
    objective: str | None = None
    original_text: str
    source_document_sha256: str
    page_index: int = Field(description="1-based physical PDF page")
    page_label: str | None = Field(default=None, description="Page number printed on the page")
    extraction_method: str = "deterministic_parser"
    extraction_confidence: float = Field(ge=0, le=1)
    parse_notes: list[str] = Field(default_factory=list, description="Source anomalies a reviewer should see")
    validation_status: ValidationStatus = ValidationStatus.PENDING_REVIEW
