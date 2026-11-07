"""Validated contracts for business documents; no application settings or DB."""

from typing import Literal

from pydantic import AwareDatetime, Field, StrictInt, field_validator, model_validator

from app.schemas.business import BusinessSchema

DocumentType = Literal["letter", "claim_response", "contract", "memo"]
Boundary = Literal["local_only", "company", "cloud"]


class DocumentCreate(BusinessSchema):
    title: str = Field(min_length=1, max_length=500)
    document_type: DocumentType
    body_text: str = Field(max_length=200000)
    counterparty: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=20)
    contact_id: StrictInt | None = Field(default=None, gt=0)
    case_evidence_id: StrictInt | None = Field(default=None, gt=0)
    owner_user_id: StrictInt | None = Field(default=None, gt=0)
    data_boundary: Boundary = "local_only"
    change_description: str | None = Field(default=None, max_length=2000)

    @field_validator("title", "body_text", "counterparty", "change_description")
    @classmethod
    def safe_text(cls, value):
        if value is not None and "\x00" in value:
            raise ValueError("invalid_text")
        return value

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError("invalid_title")
        return value

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value):
        if any(not t.strip() or len(t) > 60 or "\x00" in t for t in value) or len(set(value)) != len(value):
            raise ValueError("invalid_tags")
        return value


class StoredDocumentInput(DocumentCreate):
    """Existing 0122 rows may remain untyped while being edited."""

    document_type: DocumentType | None = None


class DocumentVersion(BusinessSchema):
    expected_revision_id: StrictInt = Field(gt=0)
    expected_updated_at: AwareDatetime


class DocumentUpdate(DocumentVersion):
    title: str | None = None
    document_type: DocumentType | None = None
    body_text: str | None = None
    counterparty: str | None = None
    tags: list[str] | None = None
    contact_id: StrictInt | None = Field(default=None, gt=0)
    case_evidence_id: StrictInt | None = Field(default=None, gt=0)
    owner_user_id: StrictInt | None = Field(default=None, gt=0)
    data_boundary: Boundary | None = None
    change_description: str | None = None


class DocumentTransition(DocumentVersion):
    expected_status: Literal["draft", "in_review", "approved", "rejected", "revision_requested"]
    target: Literal["in_review", "approved", "rejected", "revision_requested"]
    expected_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class DocumentOut(BusinessSchema):
    # Read existing 0122 rows without imposing the new creation size policy.
    title: str
    document_type: DocumentType | None
    body_text: str
    counterparty: str
    tags: list[str]
    contact_id: int | None
    case_evidence_id: int | None
    owner_user_id: int | None
    data_boundary: Boundary
    change_description: str | None
    id: int
    current_revision_id: int
    version: int
    content_hash: str
    status: Literal["draft", "in_review", "approved", "rejected", "revision_requested"]
    approval_invalidated: bool
    approved_by: str | None
    author_user_id: int | None
    updated_at: AwareDatetime


class TemplateCreate(BusinessSchema):
    name: str = Field(min_length=1, max_length=500)
    document_type: DocumentType
    content: str = Field(max_length=200000)
    variables_schema: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_template(self):
        import json

        if not self.name.strip() or "\x00" in self.name or "\x00" in self.content:
            raise ValueError("invalid_template")
        if len(json.dumps(self.variables_schema, ensure_ascii=True, allow_nan=False)) > 20000:
            raise ValueError("invalid_schema")
        return self


class TemplateOut(TemplateCreate):
    id: int
    updated_at: AwareDatetime


class TemplateUpdate(TemplateCreate):
    expected_updated_at: AwareDatetime


class TemplateVersion(BusinessSchema):
    expected_updated_at: AwareDatetime
