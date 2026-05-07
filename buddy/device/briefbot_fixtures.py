"""Briefbot V1 fixtures — hand-curated prospect data.

Phase 1 ships with these baked-in entries so the device demo works
offline. Phase 3 swaps the data source: the laptop companion pulls
from Revivn's real briefbot endpoint, and this module gets used only
as the offline fallback.

These entries are placeholders shaped for Revivn's IT-asset-disposition
pitch. Replace them with real prospects relevant to your day before
demoing — the headcount / stage / ESG details below are illustrative.

Schema: keys are normalized (lowercase, no punctuation) for fuzzy
match in briefbot_api.lookup(). Values are dicts with the keys the
device renderer in apps/briefbot.py knows how to draw.
"""

PROSPECTS = {
    "anthropic": {
        "name": "Anthropic",
        "domain": "anthropic.com",
        "employees": "1000-1500",
        "headcount_growth_yoy": "+180%",
        "stage": "Series F",
        "hq": "San Francisco, CA",
        "summary": "AI lab, Claude. Fast-growing eng org with frequent hardware refresh.",
        "talking_points": [
            "Frequent hardware turnover from rapid hiring.",
            "Responsible-AI narrative extends to ITAD / ESG reporting.",
            "Hybrid workforce; remote laptop recovery is the wedge.",
        ],
        "contacts": [
            {"name": "Workplace Tech", "title": "IT Asset Mgmt", "email": "workplace@anthropic.com"},
        ],
    },
    "applied intuition": {
        "name": "Applied Intuition",
        "domain": "appliedintuition.com",
        "employees": "501-1000",
        "headcount_growth_yoy": "+38%",
        "stage": "Series E ($6B)",
        "hq": "Mountain View, CA",
        "summary": "AV simulation and dev tools. Defense expansion ongoing.",
        "talking_points": [
            "EOL hardware refresh in Q3 fits the residual pitch.",
            "Hiring 40+ infra engineers in the Bay Area.",
            "CTO Qasar's recent post on sim infra cost.",
        ],
        "contacts": [
            {"name": "Qasar Younis", "title": "CEO", "email": "qasar@appliedintuition.com"},
        ],
    },
    "stripe": {
        "name": "Stripe",
        "domain": "stripe.com",
        "employees": "8000-10000",
        "headcount_growth_yoy": "+8%",
        "stage": "Late stage / pre-IPO",
        "hq": "South SF, CA",
        "summary": "Payments infra. Multi-office workforce, structured refresh program.",
        "talking_points": [
            "Existing ITAD vendor; entry is a sustainability angle.",
            "Recently consolidated DC ops; surplus hardware likely.",
            "Strong ESG culture, measurable diversion appeals.",
        ],
        "contacts": [
            {"name": "ITAM Lead", "title": "Sr. IT Asset Mgr", "email": "itam@stripe.com"},
        ],
    },
    "notion": {
        "name": "Notion",
        "domain": "notion.so",
        "employees": "501-1000",
        "headcount_growth_yoy": "+22%",
        "stage": "Series C ($10B)",
        "hq": "San Francisco, CA",
        "summary": "Productivity software. Mostly Mac fleet, light hardware churn.",
        "talking_points": [
            "Fleet turning 3 years in Q4; residual-value pitch fits.",
            "Engineering-led culture appreciates clean process.",
            "No public ITAD partner identified; open lane.",
        ],
        "contacts": [
            {"name": "IT Ops", "title": "Workplace Ops", "email": "it@notion.so"},
        ],
    },
    "figma": {
        "name": "Figma",
        "domain": "figma.com",
        "employees": "1000-1500",
        "headcount_growth_yoy": "+15%",
        "stage": "Pre-IPO",
        "hq": "San Francisco, CA",
        "summary": "Design tools. High-spec MacBook Pro fleet, ~$3k+ residual each.",
        "talking_points": [
            "High residual value per device; strong $/laptop pitch.",
            "S-1 timing; ESG section likely needs ITAD vendor.",
            "Designer fleet refreshes faster than industry avg.",
        ],
        "contacts": [
            {"name": "Workplace Lead", "title": "Workplace", "email": "workplace@figma.com"},
        ],
    },
    "vercel": {
        "name": "Vercel",
        "domain": "vercel.com",
        "employees": "501-1000",
        "headcount_growth_yoy": "+30%",
        "stage": "Series E ($3.25B)",
        "hq": "San Francisco, CA",
        "summary": "Frontend cloud. Heavily remote; recovery logistics is the value.",
        "talking_points": [
            "Distributed workforce; remote pickup is the differentiator.",
            "Hiring profile suggests next refresh ~6 mo out.",
            "Engineer-heavy fleet; high residual MacBook Pros.",
        ],
        "contacts": [
            {"name": "People Tech", "title": "People Ops", "email": "people-tech@vercel.com"},
        ],
    },
    "ramp": {
        "name": "Ramp",
        "domain": "ramp.com",
        "employees": "1000-1500",
        "headcount_growth_yoy": "+45%",
        "stage": "Series D ($16B)",
        "hq": "New York, NY",
        "summary": "Corporate cards and spend mgmt. Fast hiring, fast hardware churn.",
        "talking_points": [
            "Spend-mgmt buyer profile understands TCO arguments.",
            "NYC office expansion driving net-new hardware.",
            "Sustainability-conscious leadership; TCO + ESG combo.",
        ],
        "contacts": [
            {"name": "Procurement", "title": "IT Procurement", "email": "it@ramp.com"},
        ],
    },
}
