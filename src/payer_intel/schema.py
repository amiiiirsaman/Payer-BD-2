from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SalesforceProduct(str, Enum):
    SALES_CLOUD = "Sales Cloud"
    SERVICE_CLOUD = "Service Cloud"
    EXPERIENCE_CLOUD = "Experience Cloud"
    MARKETING_CLOUD = "Marketing Cloud"
    PARDOT = "Marketing Cloud Account Engagement (Pardot)"
    HEALTH_CLOUD = "Health Cloud"
    AGENTFORCE_HEALTHCARE = "Agentforce for Healthcare"
    LIFE_SCIENCES_CLOUD = "Life Sciences Cloud"
    FINANCIAL_SERVICES_CLOUD = "Financial Services Cloud"
    REVENUE_CLOUD = "Revenue Cloud (CPQ)"
    DATA_CLOUD = "Data Cloud"


PRODUCT_COLUMNS: list[str] = [p.value for p in SalesforceProduct]


class ConfidenceScore(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    REVIEW = "Requires Review"


class UsageVerdict(str, Enum):
    YES = "Yes"
    LIKELY = "Likely"
    NO = "No"
    UNKNOWN = "Unknown"


class Evidence(BaseModel):
    source_type: str  # job_posting | news | review | case_study | technographic
    url: str
    snippet: str = ""
    date: Optional[str] = None  # ISO-ish or human; recency agent normalizes
    matched_product: Optional[SalesforceProduct] = None
    full_text: Optional[str] = None  # populated by page-body enricher for high-priority URLs


class SalesforceSignal(BaseModel):
    payer_name: str
    product: SalesforceProduct
    verdict: UsageVerdict = UsageVerdict.UNKNOWN
    evidence: List[Evidence] = Field(default_factory=list)


class PayerRecord(BaseModel):
    payer_name: str
    payer_type: str = ""
    domain: str = ""
    verdicts: dict[str, str] = Field(default_factory=dict)  # product -> Yes/Likely/No/Unknown
    source_urls: List[str] = Field(default_factory=list)
    date_identified: str = ""
    confidence: ConfidenceScore = ConfidenceScore.LOW
    bd_notes: str = ""
    key_evidence: str = ""


EXCEL_COLUMNS: list[str] = [
    "Payer Name",
    "Payer Type",
    *PRODUCT_COLUMNS,
    "Source URLs",
    "Date Identified",
    "Confidence Score",
    "BD Notes",
    "Key Evidence",
]


# ─────────────────────────────────────────────────────────────────────────────
# Executive Intelligence schema (parallel pipeline; --mode executive)
# ─────────────────────────────────────────────────────────────────────────────
class ExecutiveRole(str, Enum):
    CEO = "CEO"
    CIO = "CIO"
    CMO = "CMO"
    CHIEF_MEDICAL = "Chief Medical"
    VP_EXPERIENCE = "VP Experience"


# Each persona maps to the full set of recognized titles per spec §1.
# Order matters: more-specific titles first so a "Chief Medical Officer"
# match doesn't get swallowed by a looser "Chief Officer" pattern.
EXECUTIVE_TITLE_MAP: dict[ExecutiveRole, list[str]] = {
    ExecutiveRole.CEO: [
        "Chief Executive Officer",
        "Market President",
        "Plan President",
        "President & CEO",
        "President and CEO",
        "President",
        "CEO",
    ],
    ExecutiveRole.CIO: [
        "Chief Information Officer",
        "Chief Technology Officer",
        "Chief Digital Officer",
        "Chief Digital and Information Officer",
        "Chief Information and Digital Officer",
        "CIO",
        "CTO",
        "CDO",
    ],
    ExecutiveRole.CMO: [
        "Chief Marketing Officer",
        "Chief Growth Officer",
        "VP of Sales and Marketing",
        "VP Sales and Marketing",
        "VP Marketing",
        "CMO",
    ],
    ExecutiveRole.CHIEF_MEDICAL: [
        "Chief Medical Officer",
        "Chief Clinical Officer",
        "Chief Population Health Officer",
        "Chief Health Officer",
        "CMO",  # ambiguous with Marketing; classifier disambiguates by context
    ],
    ExecutiveRole.VP_EXPERIENCE: [
        "Chief Experience Officer",
        "Chief Patient Engagement Officer",
        "Chief Member Experience Officer",
        "Chief Customer Experience Officer",
        "VP Member Experience",
        "VP of Member Experience",
        "VP Customer Experience",
        "CXO",
    ],
}


class PastJob(BaseModel):
    """One prior role for a BD warm-intro trail."""

    firm: str = ""
    title: str = ""
    years: str = ""  # e.g. "2018-2022" or "~3 years"


class ExecutiveProfile(BaseModel):
    """A single executive identified for one persona at one payer."""

    name: Optional[str] = None
    title: Optional[str] = None  # actual title as found (e.g. "Chief Digital Officer")
    linkedin_url: Optional[str] = None
    past_jobs: List[PastJob] = Field(default_factory=list)  # top 2 most recent prior roles
    departure_risk: bool = False
    departure_note: Optional[str] = None
    confidence: ConfidenceScore = ConfidenceScore.LOW
    confidence_note: Optional[str] = None
    evidence: List[Evidence] = Field(default_factory=list)


class ExecutivePayerRecord(BaseModel):
    """Final per-payer record exported by the executive pipeline."""

    payer_name: str
    payer_type: str = ""
    domain: str = ""
    executives: dict[ExecutiveRole, ExecutiveProfile] = Field(default_factory=dict)
    source_urls: List[str] = Field(default_factory=list)
    date_verified: str = ""
    confidence: ConfidenceScore = ConfidenceScore.LOW  # payer-level max() aggregate
    bd_notes: str = ""
    key_evidence: str = ""

    @property
    def aggregated_past_firms(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for role in ExecutiveRole:
            prof = self.executives.get(role)
            if not prof:
                continue
            for job in prof.past_jobs:
                key = job.firm.strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(job.firm.strip())
        return out


# Engine v2 §5 — Per-persona column groups (3 identity + 6 past-job = 9 cols each × 5 personas = 45)
EXECUTIVE_ROLE_COLUMNS: dict[ExecutiveRole, list[str]] = {
    ExecutiveRole.CEO: [
        "CEO Name", "CEO Title", "CEO LinkedIn",
        "CEO Past Job 1 Firm", "CEO Past Job 1 Title", "CEO Past Job 1 Years",
        "CEO Past Job 2 Firm", "CEO Past Job 2 Title", "CEO Past Job 2 Years",
    ],
    ExecutiveRole.CIO: [
        "CIO/CTO Name", "CIO/CTO Title", "CIO/CTO LinkedIn",
        "CIO Past Job 1 Firm", "CIO Past Job 1 Title", "CIO Past Job 1 Years",
        "CIO Past Job 2 Firm", "CIO Past Job 2 Title", "CIO Past Job 2 Years",
    ],
    ExecutiveRole.CMO: [
        "CMO/Growth Name", "CMO/Growth Title", "CMO/Growth LinkedIn",
        "CMO Past Job 1 Firm", "CMO Past Job 1 Title", "CMO Past Job 1 Years",
        "CMO Past Job 2 Firm", "CMO Past Job 2 Title", "CMO Past Job 2 Years",
    ],
    ExecutiveRole.CHIEF_MEDICAL: [
        "Chief Medical Name", "Chief Medical Title", "Chief Medical LinkedIn",
        "Chief Med Past Job 1 Firm", "Chief Med Past Job 1 Title", "Chief Med Past Job 1 Years",
        "Chief Med Past Job 2 Firm", "Chief Med Past Job 2 Title", "Chief Med Past Job 2 Years",
    ],
    ExecutiveRole.VP_EXPERIENCE: [
        "VP Experience Name", "VP Experience Title", "VP Experience LinkedIn",
        "VP Exp Past Job 1 Firm", "VP Exp Past Job 1 Title", "VP Exp Past Job 1 Years",
        "VP Exp Past Job 2 Firm", "VP Exp Past Job 2 Title", "VP Exp Past Job 2 Years",
    ],
}

# Flat 50-column list: 2 identity + 45 persona + 3 metadata.
EXECUTIVE_EXCEL_COLUMNS: list[str] = ["Payer Name", "Payer Type"]
for _role in ExecutiveRole:
    EXECUTIVE_EXCEL_COLUMNS.extend(EXECUTIVE_ROLE_COLUMNS[_role])
EXECUTIVE_EXCEL_COLUMNS.extend(["Date Verified", "Confidence Score", "BD Notes"])
