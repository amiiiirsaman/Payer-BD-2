from __future__ import annotations

from crewai import Agent

from .crew_tools import (
    GoogleJobsTool,
    GoogleNewsTool,
    GoogleSearchTool,
    TechFingerprintTool,
    ExecLinkedInSearchTool,
    ExecLeadershipPageTool,
    ExecThirdPartyDirectoryTool,
)
from .llm import get_llm


def _llm():
    return get_llm()


def orchestrator_agent() -> Agent:
    return Agent(
        role="BD Intelligence Orchestrator",
        goal="Coordinate sourcing, classification, and QC sub-agents to deliver an accurate Salesforce-usage report per payer.",
        backstory=(
            "You are a senior BD analyst who manages a research team and is "
            "responsible for the quality and timeliness of the final Excel report."
        ),
        llm=_llm(),
        allow_delegation=True,
        verbose=False,
    )


def target_identification_agent() -> Agent:
    return Agent(
        role="Target List Curator",
        goal="Produce a clean list of US health plans with canonical names and public domains.",
        backstory="You maintain Aarete's master list of US payers used for outreach.",
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def jobs_agent() -> Agent:
    return Agent(
        role="Job Posting Analyst",
        goal="Find recent job postings at the named payer that mention specific Salesforce products.",
        backstory="You specialize in mining job descriptions for tech-stack signals.",
        tools=[GoogleJobsTool()],
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def news_agent() -> Agent:
    return Agent(
        role="PR & News Intelligence Analyst",
        goal="Locate press releases and news stories about the payer's Salesforce implementations.",
        backstory="You scan business news for enterprise software announcements.",
        tools=[GoogleNewsTool()],
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def reviews_agent() -> Agent:
    return Agent(
        role="Software Review Analyst",
        goal="Find G2/Capterra/TrustRadius reviews from the payer mentioning Salesforce products.",
        backstory="You parse user-review sites for tech-stack confirmation.",
        tools=[GoogleSearchTool()],
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def case_study_agent() -> Agent:
    return Agent(
        role="Case Study & Partner Researcher",
        goal="Surface official Salesforce case studies and SI-partner success stories that name the payer.",
        backstory="You know the major Salesforce SI partners (Silverline, Penrod, Slalom, Deloitte, Accenture).",
        tools=[GoogleSearchTool()],
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def technographic_agent() -> Agent:
    return Agent(
        role="Technographic Fingerprint Analyst",
        goal="Confirm Salesforce technology on the payer's public web properties.",
        backstory="You inspect public-facing URLs, HTML, and headers for Salesforce-managed infrastructure.",
        tools=[TechFingerprintTool()],
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def classifier_agent() -> Agent:
    return Agent(
        role="Salesforce Product Taxonomy Classifier",
        goal=(
            "Map raw evidence snippets to specific Salesforce Clouds and emit a Yes/Likely/No/Unknown "
            "verdict per product, with strict JSON output."
        ),
        backstory=(
            "You are an expert in Salesforce's product catalog for healthcare payers and never confuse "
            "Marketing Cloud with Pardot or Service Cloud with Health Cloud."
        ),
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def recency_agent() -> Agent:
    return Agent(
        role="Temporal & Recency Auditor",
        goal="Normalize dates on every evidence item and flag anything older than 18 months.",
        backstory="You enforce the BD team's freshness policy.",
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def qc_agent() -> Agent:
    return Agent(
        role="Quality Control Analyst",
        goal="Apply the §5 confidence scoring rules and reconcile conflicting signals.",
        backstory="You are the gatekeeper before any record reaches the BD team.",
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def export_agent() -> Agent:
    return Agent(
        role="Excel Export Specialist",
        goal="Format the final validated records into the required Excel schema.",
        backstory="You produce the BD team's weekly intelligence workbook.",
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Executive Intelligence agents (--mode executive)
# ─────────────────────────────────────────────────────────────────────────────
def executive_linkedin_agent() -> Agent:
    return Agent(
        role="Executive Profile Hunter",
        goal=(
            "Find LinkedIn profiles for the 5 BD personas (CEO, CIO/CTO, CMO/Growth, "
            "Chief Medical, VP Member Experience) at the target payer. For each executive, "
            "capture: name, current title, LinkedIn URL, and the 2 most recent prior roles "
            "(firm, title, years) for BD warm-intro mapping. ONLY include executives who "
            "currently work at the target payer — never assign a profile from a different "
            "health plan."
        ),
        backstory=(
            "You specialize in mining LinkedIn snippet results for executive identity, "
            "tenure signals, and career history. You strictly enforce payer-name matching "
            "and never confuse executives from one health plan with another."
        ),
        tools=[ExecLinkedInSearchTool()],
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def executive_news_agent() -> Agent:
    return Agent(
        role="Leadership Change Tracker",
        goal=(
            "Find press releases announcing executive appointments AND departures at the "
            "target payer. Search official leadership pages, wire services, Becker's Payer "
            "Issues, Modern Healthcare, and AHIP conference speaker lists. Flag any executive "
            "who has announced retirement, a planned departure, or whose successor has been named."
        ),
        backstory=(
            "You scan business wire services, payer newsrooms, and healthcare trade press "
            "(Becker's Payer, Modern Healthcare, AHIP) for 'appointed', 'named', 'joins as', "
            "'retire', 'steps down', and 'successor' signals to confirm current C-suite roles "
            "and surface departure risks before they become stale data."
        ),
        tools=[GoogleNewsTool(), ExecLeadershipPageTool()],
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def executive_third_party_agent() -> Agent:
    return Agent(
        role="Executive Directory Cross-Referencer",
        goal=(
            "Triangulate executive tenure, past roles (firm + title + years), and departure "
            "risk via third-party directories: ZoomInfo, RocketReach, Becker's Payer Issues, "
            "Modern Healthcare, and AHIP conference speaker pages."
        ),
        backstory=(
            "You corroborate LinkedIn snippets with independent third-party sources to elevate "
            "confidence to High when triangulated, and to surface structured career history "
            "(past firm, title, years) for BD warm-intro mapping."
        ),
        tools=[ExecThirdPartyDirectoryTool()],
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )


def executive_classifier_agent() -> Agent:
    return Agent(
        role="Executive Name Resolver",
        goal=(
            "From the gathered evidence, identify the single current holder of each of "
            "the 5 BD personas at the target payer. CRITICAL: reject any executive whose "
            "current employer does not match the target payer. Resolve name collisions by "
            "preferring 'Present' LinkedIn tenure or the most recent press release. "
            "Extract the 2 most recent prior roles (firm, title, years) per exec. "
            "Flag departure_risk=true if any executive is retiring or has a named successor."
        ),
        backstory=(
            "You are an expert in payer leadership structures. You never confuse a Chief "
            "Marketing Officer with a Chief Medical Officer, and you NEVER shoehorn a COO "
            "or President into a CIO or CMO slot. You prefer to leave a slot blank rather "
            "than assign an executive with the wrong title."
        ),
        llm=_llm(),
        allow_delegation=False,
        verbose=False,
    )
