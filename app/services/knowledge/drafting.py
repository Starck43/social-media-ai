"""Template rendering and evidence-grounded AI drafting for business documents (P19-T02).

Core guarantees:
1. Typed template rendering (`render_template_content`): validates variable presence and types
   against `variables_schema` and template placeholders; missing required variables block rendering.
2. Evidence-grounded AI drafting (`DraftingService.draft`): resolves only actor-authorized,
   tenant- and boundary-scoped evidence (`case`, `knowledge`, `communication`, `contact`),
   produces typed paragraphs with verified `evidence_refs`, and marks/highlights any statement
   lacking verified evidence as unsupported (`[НЕ ПОДТВЕРЖДЕНО ИСТОЧНИКОМ]`).
3. Legal and financial statements are flagged `requires_human_review=True` with explicit
   `review_reasons` and recorded in `unsupported_terms_note`.
4. Drafting never auto-approves: created documents always start in `status='draft'` with
   `approved_by=None` and `decision_actor_user_id=None`.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Any, Callable, Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from app.schemas.business import BusinessSchema
from app.services.knowledge.document_contracts import Boundary, DocumentOut, DocumentType
from app.services.knowledge.documents import (
    STATUS_DRAFT,
    DocumentUnavailable,
    compute_content_hash,
    document_service,
)

UNSUPPORTED_MARKER = "[НЕ ПОДТВЕРЖДЕНО ИСТОЧНИКОМ]"
HUMAN_REVIEW_MARKER = "[ТРЕБУЕТ ПРОВЕРКИ ЧЕЛОВЕКОМ]"

_DOUBLE_BRACE_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_SINGLE_BRACE_RE = re.compile(r"(?<!\{)\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}(?!\})")
_VALID_VAR_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")

_SUPPORTED_VAR_TYPES = frozenset(
    {
        "string",
        "str",
        "text",
        "integer",
        "int",
        "number",
        "float",
        "decimal",
        "boolean",
        "bool",
        "date",
        "datetime",
    }
)

_FINANCIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "financial_amount",
        re.compile(
            r"(?:\b\d+(?:[\s.,]\d+)*\s*(?:руб(?:\.|лей|ля)?|₽|р\.|usd|\$|eur|€|тыс\.|млн)\b"
            r"|\b(?:сумм[аеуыой]|стоимост[ьиью]|оплат[аеуыой]|счет[ауом]?|счёт[ауом]?|"
            r"задолженност[ьиью]|долг[ауом]?|ндс|тариф|возврат[ау]?|компенсац[ияиюей]|"
            r"возмещен[иеияю]|убытк[иов]|процент[аыов]?|ставк[аеиу]|payment|invoice|refund|"
            r"amount|fee|interest|damages|indemnit(?:y|ies))\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "financial_penalty",
        re.compile(
            r"\b(?:штраф[аыов]?|неустойк[аеиуой]|пен[яиейю]|просрочк[аеиуой]|удержан[иеия]|"
            r"penalt(?:y|ies)|forfeiture|late\s+fee)\b",
            re.IGNORECASE,
        ),
    ),
)

_LEGAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "legal_claim_or_litigation",
        re.compile(
            r"\b(?:претензи[яиюей]|иск(?:овое|а|у|ом)?|суд(?:ебн\w+|а|у|ом)?|арбитраж\w*|"
            r"досудебн\w+|требован[иеияй]|нарушен\w+|ответственност[ьиью]|"
            r"расторжен\w+|ст\.\s*\d+|гк\s*рф|апк\s*рф|гпк\s*рф|коап|ук\s*рф|"
            r"lawsuit|litigation|arbitration|breach|liability|court|statute)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "legal_commitment_or_waiver",
        re.compile(
            r"\b(?:обязуемся|обязуется|гарантируем|гарантирует|признаем|признаём|"
            r"отказ(?:ываемся)?\s+от\s+прав|соглашен\w+|договор[ауом]?|обязательств\w*|"
            r"акцепт\w*|оферт\w*|уступк\w+|мировое\s+соглашение|"
            r"waive|waiver|admission|undertake|guarantee|settlement|binding\s+obligation)\b",
            re.IGNORECASE,
        ),
    ),
)

_PROMPT_INJECTION_RE = re.compile(
    r"(?:ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|"
    r"system\s*prompt|auto[-_\s]?approve|approve\s+without\s+review|"
    r"bypass\s+approval|отправь\s+без\s+согласования|автоматически\s+согласуй|"
    r"игнорируй\s+предыдущие\s+инструкции)",
    re.IGNORECASE,
)


class EvidenceRefOut(BusinessSchema):
    ref: str
    evidence_id: int
    context_type: Literal["case", "knowledge", "communication", "followup", "contact"]
    title: str
    citation_anchor: str
    revision_hash: str
    excerpt: str
    data_boundary: Boundary


class DraftParagraphOut(BusinessSchema):
    index: int
    text: str
    rendered_text: str
    evidence_refs: list[str] = Field(default_factory=list)
    supported: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    highlighted: bool
    requires_human_review: bool
    review_reasons: list[str] = Field(default_factory=list)


class DocumentDraftRequest(BusinessSchema):
    title: str = Field(min_length=1, max_length=500)
    document_type: DocumentType = "letter"
    data_boundary: Boundary = "cloud"
    counterparty: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=50)
    template_id: int | None = Field(default=None, gt=0)
    variables: dict[str, Any] = Field(default_factory=dict)
    contact_id: int | None = Field(default=None, gt=0)
    case_evidence_id: int | None = Field(default=None, gt=0)
    evidence_ids: list[int] = Field(default_factory=list, max_length=20)
    owner_user_id: int | None = Field(default=None, gt=0)
    instructions: str | None = Field(default=None, max_length=4000)
    change_description: str | None = Field(default=None, max_length=2000)

    @field_validator("title")
    @classmethod
    def _valid_title(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("invalid_title")
        return value

    @field_validator("counterparty", "instructions", "change_description")
    @classmethod
    def _no_nul_text(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("invalid_text")
        return value

    @field_validator("tags")
    @classmethod
    def _valid_tags(cls, value: list[str]) -> list[str]:
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item) > 64 or "\x00" in item:
                raise ValueError("invalid_tag")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def _valid_evidence_ids(cls, value: list[int]) -> list[int]:
        deduped: list[int] = []
        for item in value:
            if type(item) is not int or item <= 0:
                raise ValueError("invalid_evidence_id")
            if item not in deduped:
                deduped.append(item)
        return deduped

    @model_validator(mode="after")
    def _valid_variables(self):
        serialized = json.dumps(self.variables, ensure_ascii=True, allow_nan=False, default=str)
        if len(serialized) > 20000:
            raise ValueError("variables_too_large")
        for key, val in self.variables.items():
            if not isinstance(key, str) or not _VALID_VAR_NAME_RE.match(key):
                raise ValueError("invalid_variable_name")
            if isinstance(val, str) and "\x00" in val:
                raise ValueError("invalid_variable_value")
        return self


class DocumentDraftOut(DocumentOut):
    template_id: int | None = None
    rendered_variables: dict[str, str] = Field(default_factory=dict)
    paragraphs: list[DraftParagraphOut] = Field(default_factory=list)
    evidence: list[EvidenceRefOut] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    has_unsupported_claims: bool = False
    requires_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    human_review_notice: str = ""
    unsupported_terms_note: str = ""


def _parse_variable_specs(variables_schema: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Normalize a template `variables_schema` into `{name: {'type': ..., 'required': ..., 'default': ...}}`."""
    if not variables_schema:
        return {}
    if not isinstance(variables_schema, dict):
        raise ValueError("invalid_schema")

    # Support JSON-Schema style `{"type": "object", "properties": {...}, "required": [...]}`
    if "properties" in variables_schema and isinstance(variables_schema.get("properties"), dict):
        props = variables_schema["properties"]
        req_list = variables_schema.get("required") or []
        if not isinstance(req_list, (list, tuple, set)):
            raise ValueError("invalid_schema")
        req_set = {str(item) for item in req_list}
        specs: dict[str, dict[str, Any]] = {}
        for name, spec in props.items():
            if not isinstance(name, str) or not _VALID_VAR_NAME_RE.match(name):
                raise ValueError("invalid_schema")
            if isinstance(spec, str):
                var_type = spec.strip().lower()
                required = name in req_set or True
                has_default = False
                default_val = None
            elif isinstance(spec, dict):
                var_type = str(spec.get("type", "string")).strip().lower()
                required = bool(spec.get("required", name in req_set if req_set else True))
                has_default = "default" in spec
                default_val = spec.get("default")
            else:
                raise ValueError("invalid_schema")
            if var_type not in _SUPPORTED_VAR_TYPES:
                raise ValueError(f"unsupported_variable_type:{var_type}")
            specs[name] = {
                "type": var_type,
                "required": required,
                "has_default": has_default,
                "default": default_val,
            }
        return specs

    specs = {}
    for name, raw_spec in variables_schema.items():
        if not isinstance(name, str) or not _VALID_VAR_NAME_RE.match(name):
            raise ValueError("invalid_schema")
        if isinstance(raw_spec, str):
            var_type = raw_spec.strip().lower()
            required = True
            has_default = False
            default_val = None
        elif isinstance(raw_spec, dict):
            var_type = str(raw_spec.get("type", "string")).strip().lower()
            required = bool(raw_spec.get("required", True))
            has_default = "default" in raw_spec
            default_val = raw_spec.get("default")
        else:
            raise ValueError("invalid_schema")
        if var_type not in _SUPPORTED_VAR_TYPES:
            raise ValueError(f"unsupported_variable_type:{var_type}")
        specs[name] = {
            "type": var_type,
            "required": required,
            "has_default": has_default,
            "default": default_val,
        }
    return specs


def _validate_and_format_variable(name: str, value: Any, var_type: str) -> str:
    """Validate `value` against `var_type` and return its deterministic string representation."""
    if value is None:
        raise ValueError(f"missing_template_variable:{name}")
    if var_type in {"string", "str", "text"}:
        if not isinstance(value, str):
            raise ValueError(f"invalid_variable_type:{name}:expected_string")
        if "\x00" in value or not value.strip():
            raise ValueError(f"invalid_variable_value:{name}")
        return value.strip()
    if var_type in {"integer", "int"}:
        if type(value) is not int:
            raise ValueError(f"invalid_variable_type:{name}:expected_integer")
        return str(value)
    if var_type in {"number", "float", "decimal"}:
        if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError(f"invalid_variable_type:{name}:expected_number")
        return str(value)
    if var_type in {"boolean", "bool"}:
        if type(value) is not bool:
            raise ValueError(f"invalid_variable_type:{name}:expected_boolean")
        return "да" if value else "нет"
    if var_type == "date":
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str):
            try:
                return date.fromisoformat(value.strip()).isoformat()
            except ValueError:
                raise ValueError(f"invalid_variable_type:{name}:expected_date") from None
        raise ValueError(f"invalid_variable_type:{name}:expected_date")
    if var_type == "datetime":
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).isoformat()
            except ValueError:
                raise ValueError(f"invalid_variable_type:{name}:expected_datetime") from None
        raise ValueError(f"invalid_variable_type:{name}:expected_datetime")
    raise ValueError(f"unsupported_variable_type:{var_type}")


def extract_template_placeholders(content: str) -> list[str]:
    """Return ordered unique variable names referenced via `{{ name }}` or `{name}`."""
    seen: list[str] = []
    for match in _DOUBLE_BRACE_RE.finditer(content or ""):
        var_name = match.group(1)
        if var_name not in seen:
            seen.append(var_name)
    for match in _SINGLE_BRACE_RE.finditer(content or ""):
        var_name = match.group(1)
        if var_name not in seen:
            seen.append(var_name)
    return seen


def render_template_content(
    content: str,
    variables_schema: dict[str, Any] | None,
    variables: dict[str, Any] | None,
) -> tuple[str, dict[str, str]]:
    """Render a template with typed variables; missing variables block rendering (`ValueError`)."""
    if "\x00" in (content or ""):
        raise ValueError("invalid_template_content")
    provided = dict(variables or {})
    specs = _parse_variable_specs(variables_schema)
    placeholders = extract_template_placeholders(content or "")

    # Every placeholder in `content` is required unless `specs[name]` explicitly sets `required=False` with a default.
    for placeholder in placeholders:
        if placeholder not in specs:
            specs[placeholder] = {
                "type": "string",
                "required": True,
                "has_default": False,
                "default": None,
            }

    missing: list[str] = []
    formatted: dict[str, str] = {}

    for name, spec in specs.items():
        if name in provided and provided[name] is not None:
            raw_val = provided[name]
            if isinstance(raw_val, str) and not raw_val.strip() and spec["required"]:
                missing.append(name)
                continue
            formatted[name] = _validate_and_format_variable(name, raw_val, spec["type"])
        elif spec["has_default"] and spec["default"] is not None:
            formatted[name] = _validate_and_format_variable(name, spec["default"], spec["type"])
        elif not spec["required"]:
            formatted[name] = ""
        else:
            missing.append(name)

    # Also validate any extra variables supplied by the caller even if not in `specs`
    for extra_name, extra_val in provided.items():
        if extra_name not in specs:
            if not _VALID_VAR_NAME_RE.match(str(extra_name)):
                raise ValueError(f"invalid_variable_name:{extra_name}")
            if extra_val is None or (isinstance(extra_val, str) and not extra_val.strip()):
                missing.append(extra_name)
            elif isinstance(extra_val, bool):
                formatted[extra_name] = _validate_and_format_variable(extra_name, extra_val, "boolean")
            elif type(extra_val) is int:
                formatted[extra_name] = _validate_and_format_variable(extra_name, extra_val, "integer")
            elif isinstance(extra_val, float):
                formatted[extra_name] = _validate_and_format_variable(extra_name, extra_val, "number")
            else:
                formatted[extra_name] = _validate_and_format_variable(extra_name, str(extra_val), "string")

    if missing:
        missing_sorted = ", ".join(sorted(set(missing)))
        raise ValueError(f"missing_template_variables:{missing_sorted}")

    rendered = _DOUBLE_BRACE_RE.sub(lambda m: formatted.get(m.group(1), ""), content or "")
    rendered = _SINGLE_BRACE_RE.sub(lambda m: formatted.get(m.group(1), ""), rendered)
    return rendered, formatted


def detect_legal_financial_flags(text: str) -> list[str]:
    """Detect legal or financial statements that require mandatory human review."""
    clean = (text or "").strip()
    if not clean:
        return []
    reasons: list[str] = []
    for code, pattern in _FINANCIAL_PATTERNS:
        if pattern.search(clean) and code not in reasons:
            reasons.append(code)
    for code, pattern in _LEGAL_PATTERNS:
        if pattern.search(clean) and code not in reasons:
            reasons.append(code)
    if _PROMPT_INJECTION_RE.search(clean) and "prompt_injection_attempt" not in reasons:
        reasons.append("prompt_injection_attempt")
    return reasons


def evaluate_draft_paragraphs(
    raw_paragraphs: list[dict[str, Any]],
    allowed_evidence: dict[str, EvidenceRefOut],
    *,
    document_type: str = "letter",
) -> tuple[list[DraftParagraphOut], str, list[str], list[str], str]:
    """Verify paragraph evidence refs, mark/highlight unsupported claims, and flag legal/financial statements.

    Returns `(paragraphs, assembled_body_text, unsupported_claims, review_reasons, unsupported_terms_note)`.
    """
    evaluated: list[DraftParagraphOut] = []
    all_unsupported: list[str] = []
    all_review_reasons: list[str] = []
    rendered_blocks: list[str] = []

    # Claim responses and contracts inherently carry legal review requirements
    if document_type in {"claim_response", "contract"}:
        all_review_reasons.append(f"document_type:{document_type}")

    for idx, item in enumerate(raw_paragraphs, start=1):
        raw_text = str(item.get("text") or "").strip()
        if not raw_text:
            continue
        if "\x00" in raw_text:
            raise ValueError("invalid_paragraph_text")

        requested_refs = item.get("evidence_refs") or []
        valid_refs: list[str] = []
        if isinstance(requested_refs, (list, tuple)):
            for ref in requested_refs:
                ref_str = str(ref or "").strip()
                if ref_str in allowed_evidence and ref_str not in valid_refs:
                    valid_refs.append(ref_str)

        explicit_unsupported = [
            str(claim).strip()
            for claim in (item.get("unsupported_claims") or [])
            if str(claim or "").strip()
        ]
        # Any statement without verified evidence is marked as unsupported
        is_supported = bool(valid_refs) and not explicit_unsupported
        para_unsupported = list(explicit_unsupported)
        if not valid_refs and raw_text not in para_unsupported:
            para_unsupported.append(raw_text)

        highlighted = not is_supported
        para_reasons = detect_legal_financial_flags(raw_text)
        if not is_supported and "unsupported_statement" not in para_reasons:
            para_reasons.append("unsupported_statement")

        requires_review = bool(para_reasons) or document_type in {"claim_response", "contract"}
        for reason in para_reasons:
            if reason not in all_review_reasons:
                all_review_reasons.append(reason)
        for claim in para_unsupported:
            if claim not in all_unsupported:
                all_unsupported.append(claim)

        cite_suffix = f" [Источники: {', '.join(valid_refs)}]" if valid_refs else ""
        review_suffix = f" {HUMAN_REVIEW_MARKER}" if detect_legal_financial_flags(raw_text) else ""
        if highlighted:
            rendered_line = f"> **{UNSUPPORTED_MARKER}** {raw_text}{review_suffix}"
        else:
            rendered_line = f"{raw_text}{cite_suffix}{review_suffix}"

        rendered_blocks.append(rendered_line)
        evaluated.append(
            DraftParagraphOut(
                index=idx,
                text=raw_text,
                rendered_text=rendered_line,
                evidence_refs=valid_refs,
                supported=is_supported,
                unsupported_claims=para_unsupported,
                highlighted=highlighted,
                requires_human_review=requires_review,
                review_reasons=para_reasons,
            )
        )

    note_parts: list[str] = []
    if all_unsupported:
        preview = "; ".join(claim[:120] for claim in all_unsupported[:3])
        note_parts.append(
            f"Неподтверждённые утверждения ({len(all_unsupported)}): {preview}"
        )
    if all_review_reasons:
        note_parts.append(
            f"Требуется проверка человеком ({', '.join(all_review_reasons)})"
        )
    unsupported_terms_note = " | ".join(note_parts)
    body_text = "\n\n".join(rendered_blocks)
    return evaluated, body_text, all_unsupported, all_review_reasons, unsupported_terms_note


def _synthesize_paragraphs_from_context(
    *,
    title: str,
    document_type: str,
    counterparty: str,
    rendered_template: str | None,
    instructions: str | None,
    evidence_list: list[EvidenceRefOut],
) -> list[dict[str, Any]]:
    """Deterministic baseline synthesis when no external LLM callable overrides generation."""
    paragraphs: list[dict[str, Any]] = []
    primary_refs = [ev.ref for ev in evidence_list]

    if rendered_template:
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", rendered_template) if chunk.strip()]
        for chunk in chunks:
            paragraphs.append(
                {
                    "text": chunk,
                    "evidence_refs": list(primary_refs),
                }
            )
    else:
        header_target = f" в адрес {counterparty}" if counterparty else ""
        doc_label = {
            "letter": "Деловое письмо",
            "claim_response": "Ответ на претензию",
            "contract": "Проект договора",
            "memo": "Служебная записка",
        }.get(document_type, "Деловой документ")
        if evidence_list:
            for ev in evidence_list:
                paragraphs.append(
                    {
                        "text": f"{doc_label} «{title}»{header_target}: по данным источника «{ev.title}» ({ev.citation_anchor}) — {ev.excerpt}",
                        "evidence_refs": [ev.ref],
                    }
                )
        else:
            paragraphs.append(
                {
                    "text": f"{doc_label} «{title}»{header_target}: подготовлен без привязанных источников контекста.",
                    "evidence_refs": [],
                }
            )

    if instructions and instructions.strip():
        clean_instr = instructions.strip()
        # Check if instructions state facts not backed by evidence
        matched_refs = [
            ev.ref
            for ev in evidence_list
            if any(
                token in ev.excerpt.lower() or token in ev.title.lower()
                for token in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{4,}", clean_instr.lower())
            )
        ]
        if _PROMPT_INJECTION_RE.search(clean_instr):
            matched_refs = []
        paragraphs.append(
            {
                "text": clean_instr,
                "evidence_refs": matched_refs,
            }
        )

    return paragraphs


class DraftingService:
    """Permission-checked template rendering and evidence-grounded AI document drafting."""

    async def _collect_evidence(
        self,
        session,
        data: DocumentDraftRequest,
        identity: tuple[int, int, str | None, tuple[str, ...]],
    ) -> tuple[list[EvidenceRefOut], str]:
        from sqlalchemy import select

        from app.core.access_catalog import workspace_grants
        from app.core.permissions import get_current_user, has_permission
        from app.models import Contact, KnowledgeSnippet
        from app.models.managers.knowledge_manager import BOUNDARIES
        from app.types import ActionType

        tenant_id, _, role, scopes = identity
        allowed_boundaries = BOUNDARIES[data.data_boundary]
        evidence_items: list[EvidenceRefOut] = []
        resolved_counterparty = data.counterparty.strip()

        # 1. Contact (CRM context)
        if data.contact_id is not None:
            if ("contact", ActionType.VIEW) not in workspace_grants(role) or not has_permission(
                get_current_user(), "contact", ActionType.VIEW
            ):
                raise PermissionError("document_contact_denied")
            contact = await session.scalar(
                select(Contact)
                .where(
                    Contact.tenant_id == tenant_id,
                    Contact.id == data.contact_id,
                    Contact.merged_into_id.is_(None),
                )
                .with_for_update(read=True)
            )
            if contact is None:
                raise DocumentUnavailable("document_unavailable")
            contact_boundary = getattr(contact, "data_boundary", None) or data.data_boundary
            if contact_boundary not in allowed_boundaries:
                raise DocumentUnavailable("document_unavailable")
            display_name = str(
                getattr(contact, "display_name_encrypted", None)
                or getattr(contact, "name", None)
                or f"Контакт #{contact.id}"
            ).strip()[:200]
            if not resolved_counterparty:
                resolved_counterparty = display_name
            channel = getattr(contact, "preferred_channel", None) or getattr(contact, "last_active_channel", None)
            excerpt = f"Контрагент: {display_name}" + (f" (канал: {channel})" if channel else "")
            evidence_items.append(
                EvidenceRefOut(
                    ref=f"contact:{contact.id}",
                    evidence_id=int(contact.id),
                    context_type="contact",
                    title=display_name,
                    citation_anchor=f"contact:{contact.id}",
                    revision_hash=f"pii:{getattr(contact, 'pii_updated_at', 1)}",
                    excerpt=excerpt,
                    data_boundary=contact_boundary,
                )
            )

        # 2. Case evidence snippet (`case_evidence_id`)
        snippet_ids: list[tuple[int, bool]] = []
        if data.case_evidence_id is not None:
            snippet_ids.append((data.case_evidence_id, True))
        for eid in data.evidence_ids:
            if eid != data.case_evidence_id:
                snippet_ids.append((eid, False))

        for snippet_id, require_case in snippet_ids:
            clauses = [
                KnowledgeSnippet.tenant_id == tenant_id,
                KnowledgeSnippet.id == snippet_id,
                KnowledgeSnippet.acl_scope.in_(scopes),
                KnowledgeSnippet.data_boundary.in_(allowed_boundaries),
            ]
            if require_case:
                clauses.append(KnowledgeSnippet.context_type == "case")
            snippet = await session.scalar(select(KnowledgeSnippet).where(*clauses).with_for_update(read=True))
            if snippet is None:
                raise DocumentUnavailable("document_unavailable")
            ctx_type = str(getattr(snippet, "context_type", "knowledge") or "knowledge")
            ref_key = f"{ctx_type}:{snippet.id}"
            evidence_items.append(
                EvidenceRefOut(
                    ref=ref_key,
                    evidence_id=int(snippet.id),
                    context_type=ctx_type if ctx_type in {"case", "knowledge", "communication", "followup"} else "knowledge",
                    title=str(snippet.title or f"Источник #{snippet.id}")[:200],
                    citation_anchor=str(snippet.citation_anchor or snippet.source_ref or ref_key),
                    revision_hash=str(snippet.revision_hash or "sha256:0"),
                    excerpt=str(snippet.excerpt or "").strip()[:1000],
                    data_boundary=snippet.data_boundary,
                )
            )

        return evidence_items, resolved_counterparty

    async def draft(
        self,
        data: DocumentDraftRequest | dict[str, Any],
        *,
        llm_callable: Callable[[dict[str, Any]], Any] | None = None,
        session=None,
    ) -> DocumentDraftOut:
        if session is None:
            from app.core.database import with_db_session

            return await with_db_session(type(self).draft)(self, data, llm_callable=llm_callable)

        from app.models import DocumentRevision, KnowledgeDocument
        from app.types import ActionType

        req = DocumentDraftRequest.model_validate(
            data.model_dump() if isinstance(data, DocumentDraftRequest) else data
        )
        identity = await document_service._actor(session, ActionType.CREATE)

        # Verify owner_user_id and links via document_service._references
        await document_service._references(session, req, identity)

        # 1. Optional template lookup & strict variable rendering
        rendered_template: str | None = None
        rendered_vars: dict[str, str] = {}
        if req.template_id is not None:
            template = await document_service._template(session, req.template_id, identity)
            rendered_template, rendered_vars = render_template_content(
                template.content,
                template.variables_schema,
                req.variables,
            )
        elif req.variables:
            # Validate variables even when no template_id is given
            _, rendered_vars = render_template_content("", {}, req.variables)

        # 2. Collect authorized case/CRM/knowledge evidence within actor's ACL & data_boundary
        evidence_list, resolved_counterparty = await self._collect_evidence(session, req, identity)
        allowed_evidence = {ev.ref: ev for ev in evidence_list}

        # 3. Run LLM pipeline (if provided and permitted by boundary/injection gates) or baseline synthesis
        raw_paragraphs: list[dict[str, Any]] | None = None
        injection_detected = bool(req.instructions and _PROMPT_INJECTION_RE.search(req.instructions))

        if llm_callable is not None and not injection_detected:
            llm_payload = {
                "title": req.title,
                "document_type": req.document_type,
                "data_boundary": req.data_boundary,
                "counterparty": resolved_counterparty,
                "rendered_template": rendered_template,
                "instructions": req.instructions,
                "evidence": [ev.model_dump() for ev in evidence_list],
            }
            llm_result = llm_callable(llm_payload)
            if hasattr(llm_result, "__await__"):
                llm_result = await llm_result
            if isinstance(llm_result, dict) and isinstance(llm_result.get("paragraphs"), list):
                raw_paragraphs = llm_result["paragraphs"]

        if not raw_paragraphs:
            raw_paragraphs = _synthesize_paragraphs_from_context(
                title=req.title,
                document_type=req.document_type,
                counterparty=resolved_counterparty,
                rendered_template=rendered_template,
                instructions=req.instructions,
                evidence_list=evidence_list,
            )

        # 4. Evaluate paragraphs, mark/highlight unsupported claims, and flag legal/financial statements
        paragraphs, body_text, unsupported_claims, review_reasons, unsupported_terms_note = (
            evaluate_draft_paragraphs(
                raw_paragraphs,
                allowed_evidence,
                document_type=req.document_type,
            )
        )
        if not body_text.strip():
            raise ValueError("empty_draft_body")

        requires_human_review = bool(review_reasons) or bool(unsupported_claims)
        human_review_notice = (
            "Черновик требует обязательной проверки человеком перед согласованием: "
            + ", ".join(review_reasons)
            if requires_human_review
            else ""
        )

        # 5. Persist as a new draft document (never auto-approved)
        doc = KnowledgeDocument(
            tenant_id=identity[0],
            title=req.title,
            document_type=req.document_type,
            counterparty=resolved_counterparty,
            tags=req.tags,
            contact_id=req.contact_id,
            case_evidence_id=req.case_evidence_id,
            owner_user_id=req.owner_user_id if req.owner_user_id is not None else identity[1],
            data_boundary=req.data_boundary,
            source_ref=f"document:{uuid4()}",
            citation_anchor="",
            acl_scope="collection.source.view",
        )
        session.add(doc)
        await session.flush()

        revision = DocumentRevision(
            tenant_id=identity[0],
            document_id=doc.id,
            version=1,
            body_text=body_text,
            content_hash=compute_content_hash(body_text),
            status=STATUS_DRAFT,
            author="",
            author_user_id=identity[1],
            change_description=req.change_description or "Черновик создан через AI/шаблон (P19-T02)",
            approved_by=None,
            decision_actor_user_id=None,
            unsupported_terms_note=unsupported_terms_note,
        )
        session.add(revision)
        await session.flush()
        await session.refresh(doc)

        projected = await document_service._output(session, doc, revision, identity)
        return DocumentDraftOut(
            **projected.model_dump(),
            template_id=req.template_id,
            rendered_variables=rendered_vars,
            paragraphs=paragraphs,
            evidence=evidence_list,
            unsupported_claims=unsupported_claims,
            has_unsupported_claims=bool(unsupported_claims),
            requires_human_review=requires_human_review,
            review_reasons=review_reasons,
            human_review_notice=human_review_notice,
            unsupported_terms_note=unsupported_terms_note,
        )


drafting_service = DraftingService()
