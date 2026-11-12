# Product phases

[Parent: product directions](../product/PRODUCT_PLAN.md#foundation-directions). Each phase links active cards and accepted audits; ROADMAP alone owns task status.

| Phase | Plan |
| --- | --- |
| P1 | [Truthful queue execution](p1-queue-execution.md) |
| P2 | [Complete usage and spend](p2-usage-spend.md) |
| P3 | [Identity and approvals](p3-identity-access.md) |
| P4 | [Tenant-safe delivery](p4-delivery.md) |
| P5 | [Safe AI boundaries](p5-ai-boundaries.md) |
| P6 | [Lifecycle and privacy](p6-lifecycle-privacy.md) |
| P7 | [Reproducible operation](p7-operations.md) |
| P8 | [First value and support UI](p8-first-value-ui.md) |
| P9 | [Safe activation and platform access](p9-safe-activation.md) |
| P10 | [Source-audit hardening](p10-audit-hardening.md) |
| P11 | [Structure, interfaces and scale readiness](p11-structure.md) |
| P12 | [Domain workspace and navigation expansion](p12-domain-workspace-expansion.md) |
| P13 | [Business-module foundation](p13-business-foundation.md) |
| P14 | [Bitrix24 CRM integration](p14-bitrix24-crm.md) |
| P15 | [Unified communication hub](p15-communication-hub.md) |
| P16 | [Planner tasks and calendar sync](p16-planner-calendar.md) |
| P17 | [1C:Enterprise integration](p17-onec-erp.md) |
| P18 | [Local file connector](p18-local-file-connector.md) |
| P19 | [Document management](p19-document-management.md) |
| P21 | [AI model operations and dispatch safety](p21-ai-operations.md) |

P13–P19 implement the owner's business-module TZ; the adapted specification,
review findings and owner decisions are in [BUSINESS_MODULES](../product/BUSINESS_MODULES.md).

A phase has a goal, action plan, product backlink and task/audit links. Close it
when all plan items are accepted; retain a dry outcome summary, completion date,
history pointer and accepted SHA with its commit date. Unknown legacy metadata
stays in the linked evidence rather than being invented. Detailed historical
evidence is lazy-loaded from `docs/audit/`; do not copy transcripts or statuses.
