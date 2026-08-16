"""Drop-in replacement for intern_engine.filters gates, tuned for
security + network/infrastructure internships.

Swap points in the upstream engine (zshah101 internship engine, MIT):
    filters.is_tech(title)   -> is_relevant(title)
    filters.categorize(title)-> categorize(title)

Keep upstream's is_internship(), season/cycle, and posted-date logic as-is.
Add track_of(title) to split the feed into per-person tables.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# 1. SECURITY — upstream only catches cyber/infosec/appsec/"security engineer",
#    which silently drops "Network Security Intern", "SOC Analyst Intern",
#    "GRC Intern", "Threat Intelligence Intern", etc.
# --------------------------------------------------------------------------
SECURITY_RE = re.compile(
    r"\b("
    r"cyber|cybersecurity|infosec|appsec|"
    r"information security|application security|product security|"
    r"cloud security|network security|offensive security|security"
    r"(?:\s+(?:engineer|engineering|analyst|operations|architect|"
    r"research|researcher|intern|internship|consultant|specialist))?|"
    r"soc\s+analyst|security operations|"
    r"threat\s+(?:intel|intelligence|hunting|detection|research)|"
    r"detection\s+(?:engineer|engineering)|incident response|"
    r"vulnerability\s+(?:management|research|assessment)|"
    r"penetration\s+test(?:ing|er)?|pen\s?test(?:ing|er)?|red team|blue team|"
    r"malware|forensics|digital forensics|dfir|"
    r"identity\s+and\s+access|iam\b|zero trust|"
    r"grc\b|governance,?\s+risk|risk and compliance|it\s+audit|"
    r"compliance\s+(?:analyst|engineer)|nist|fedramp|"
    r"trust\s+and\s+safety|fraud\s+(?:analyst|engineer|detection)"
    r")\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# 2. NETWORK / INFRASTRUCTURE — the category upstream has no concept of at all.
#    These titles are why networking internships "never drop": they do drop,
#    they just never match a software-shaped include list.
# --------------------------------------------------------------------------
NETWORK_RE = re.compile(
    r"\b("
    r"network|networking|noc\b|network operations|"
    r"telecom|telecommunications|wireless|rf engineer|ran\b|5g\b|"
    r"voice engineer|voip|unified communications|"
    r"fiber|optical network|transport network|sdn\b|"
    r"routing|switching|bgp|ospf|"
    r"infrastructure|it infrastructure|enterprise infrastructure|"
    r"systems?\s+(?:admin|administrator|administration|engineer|engineering|"
    r"analyst|operations)|"
    r"sysadmin|server (?:admin|engineer)|"
    r"data\s?cent(?:er|re)|"
    # "cloud" + any following word, not a fixed list of five suffixes. The old
    # list dropped "Cloud Solutions Intern", "Public Cloud Intern" and
    # "Cloud Architect Intern" outright; the provider names catch the titles
    # that never say "cloud" at all. Sales/marketing "cloud" titles are still
    # rejected upstream by NON_TECH_RE and FALSE_NETWORK_RE.
    r"cloud\s+\w+|azure|aws|gcp|google\s+cloud|kubernetes|"
    r"devops|devsecops|sre\b|site reliability|platform engineer(?:ing)?|"
    r"linux|windows engineer|active directory|virtualization|vmware|"
    r"technical support engineer|desktop support|help\s?desk|service desk|"
    r"it support|end user (?:support|computing)|"
    r"technology (?:analyst|associate|operations)|"
    r"it\s+(?:intern|internship|operations|analyst|engineer|generalist)"
    r")\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# 3. EXCLUSIONS — narrower than upstream's. Upstream's hardware blocklist
#    ("optical", "wireless", "electrical") is exactly what kills telecom and
#    RF roles, so hardware terms only exclude when nothing else matched.
# --------------------------------------------------------------------------
SENIOR_RE = re.compile(
    r"\b(senior|sr\.?|staff|principal|manager|director|lead|vp|head)\b",
    re.IGNORECASE,
)

NON_TECH_RE = re.compile(
    r"\b("
    r"recruit(?:ing|er|ment)?|talent acquisition|human resources|"
    r"sales|account executive|account manager|business development|"
    r"marketing|brand|social media|content creator|copywriter|"
    r"legal|counsel|paralegal|accounting|payroll|tax|audit associate|"
    r"supply chain|logistics|warehouse|procurement|"
    r"graphic design|industrial design|ux design|ui design|"
    r"nursing|clinical|pharmacy|teacher|tutor"
    r")\b",
    re.IGNORECASE,
)

# "Network" also means people-networking and TV networks. Kill those.
FALSE_NETWORK_RE = re.compile(
    r"\b("
    r"merchant.{0,15}network services|network services sales|"
    r"broadcast network|television network|"
    r"professional network(?:ing)?|networking event|"
    r"social network(?:ing)? (?:marketing|strategy)|"
    r"network development (?:representative|manager)|"  # healthcare provider networks
    r"provider network|payer network|"
    # Broadening "cloud" to any following word puts go-to-market titles in
    # range. NON_TECH_RE already stops sales/marketing/account wording; these
    # cover the partner/channel variants it has no word for.
    r"cloud\s+(?:sales|marketing|account|partner|partnerships|channel)"
    r")\b",
    re.IGNORECASE,
)

HARD_HARDWARE_RE = re.compile(
    r"\b("
    r"mechanical|aerospace|aeronautical|propulsion|thermal|structural|"
    r"chemical|chemist|biology|biological|materials|civil engineer|"
    r"asic|vlsi|rtl design|pcb|analog design|semiconductor process"
    r")\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# 3b. SOFTWARE-ENGINEERING VETO — the cost of matching "cloud" generously.
#     Amazon's "Software Development Engineer Intern, AWS Data Services" is a
#     SWE role that happens to name a cloud product, and it was being kept and
#     labelled Cloud Platform. A veto is the right shape here rather than
#     narrowing the cloud pattern again: the title says what the job IS.
# --------------------------------------------------------------------------
SWE_VETO_RE = re.compile(
    r"\b("
    r"software\s+engineer(?:ing)?|software\s+develop(?:er|ment)|sde\b|"
    r"back\s?end|front[\s-]?end|full[\s-]?stack|"
    r"application\s+(?:developer|engineer)|"
    r"machine\s+learning|ml\s+engineer|research\s+engineer|"
    r"data\s+scientist|data\s+engineer|"
    r"mobile\s+engineer|ios\b|android"
    r")\b",
    re.IGNORECASE,
)

# What overrides the veto: terms that are specific to THIS domain. Deliberately
# narrower than NETWORK_RE — "cloud", "platform" and "systems" are exactly the
# words a SWE title borrows, so they must not rescue one. "Network Software
# Engineer Intern" is a networking job; "SDE, AWS Data Services" is not.
NET_DOMAIN_RE = re.compile(
    r"\b("
    r"network\w*|noc\b|telecom\w*|wireless|rf\b|fiber|optical\s+network|"
    r"routing|switching|bgp|ospf|"
    r"sysadmin|systems?\s+admin\w*|data\s?cent(?:er|re)|"
    r"infrastructure|devops|devsecops|sre\b|site\s+reliability"
    r")\b",
    re.IGNORECASE,
)

INTERN_RE = re.compile(
    r"\b(intern|interns|internship|co[\s-]?op|cooperative\s+education|"
    r"apprentice(?:ship)?|summer analyst|campus)\b",
    re.IGNORECASE,
)


def is_internship(title: str) -> bool:
    if not title:
        return False
    return bool(INTERN_RE.search(title)) and not SENIOR_RE.search(title)


def is_relevant(title: str) -> bool:
    """Keep security + network/infra/IT roles. Reject everything else."""
    if not title:
        return False
    if NON_TECH_RE.search(title) or FALSE_NETWORK_RE.search(title):
        return False
    hit_sec = bool(SECURITY_RE.search(title))
    hit_net = bool(NETWORK_RE.search(title))
    if not (hit_sec or hit_net):
        return False
    # A software-engineering title stays out even when it names a cloud
    # product, unless it also names security or a term specific to this
    # domain — that combination is a real infrastructure job, not a SWE role
    # wearing an "AWS" suffix.
    if SWE_VETO_RE.search(title) and not (hit_sec or NET_DOMAIN_RE.search(title)):
        return False
    # Hardware terms only veto when the role isn't clearly sec/net anyway.
    if HARD_HARDWARE_RE.search(title) and not (hit_sec or hit_net):
        return False
    return True


# --------------------------------------------------------------------------
# 4. Categories + per-person tracks
# --------------------------------------------------------------------------
_SUBCATS = [
    ("SOC / Detection", re.compile(
        r"\b(soc\b|security operations|detection|threat|incident response|"
        r"forensics|dfir|malware|hunting)\b", re.IGNORECASE)),
    ("AppSec / Product Sec", re.compile(
        r"\b(appsec|application security|product security|secure code|"
        r"penetration|pentest|red team|offensive)\b", re.IGNORECASE)),
    ("GRC / Risk", re.compile(
        r"\b(grc|governance|risk and compliance|it audit|compliance|"
        r"nist|fedramp|policy)\b", re.IGNORECASE)),
    ("Cloud & Infra Sec", re.compile(
        r"\b(cloud security|network security|infrastructure security|"
        r"identity|iam|zero trust)\b", re.IGNORECASE)),
    ("Security (general)", SECURITY_RE),
    ("Network / Telecom", re.compile(
        r"\b(network|networking|noc|telecom\w*|wireless|rf|5g|ran|fiber|"
        r"optical|routing|switching|voip|voice)", re.IGNORECASE)),
    # Above "Systems & Cloud Infra" on purpose: a title that names a cloud
    # platform is a cloud role first, and grouping it with sysadmin/server work
    # hid the fastest-growing slice of the list inside a generic bucket.
    ("Cloud Platform", re.compile(
        r"\b(cloud|azure|aws|gcp|google cloud|kubernetes)\b", re.IGNORECASE)),
    ("Systems & Cloud Infra", re.compile(
        r"\b(infrastructure|systems?|sysadmin|server|data\s?cent\w*|cloud|"
        r"devops|sre|site reliability|platform|linux|windows|"
        r"virtualization|active directory)\b", re.IGNORECASE)),
    ("IT Support / Ops", re.compile(
        r"\b(help\s?desk|service desk|it support|desktop support|"
        r"technical support|end user|technology analyst|it intern)\b",
        re.IGNORECASE)),
]


def categorize(title: str) -> str:
    if not title:
        return "Other"
    for name, pattern in _SUBCATS:
        if pattern.search(title):
            return name
    return "Other"


TRACKS = {
    "security": {
        "SOC / Detection", "AppSec / Product Sec", "GRC / Risk",
        "Cloud & Infra Sec", "Security (general)",
    },
    "network": {
        "Network / Telecom", "Cloud Platform", "Systems & Cloud Infra",
        "IT Support / Ops",
    },
}


def tracks_of(title: str) -> tuple[str, ...]:
    """Which table(s) this role belongs in — possibly both.

    A title naming a security term AND a cloud/network term ("Cloud Security
    Intern", "Network Security Intern") is genuinely both people's job. Filing
    it under security alone meant the networking reader never saw a role
    written for them, so it renders in both tables instead.
    """
    if not title:
        return ("other",)
    if SECURITY_RE.search(title) and NETWORK_RE.search(title):
        return ("security", "network")
    cat = categorize(title)
    for track, cats in TRACKS.items():
        if cat in cats:
            return (track,)
    return ("other",)


def track_of(title: str) -> str:
    """The single primary track, security first. tracks_of() is what the
    renderer uses — a role can legitimately belong to both."""
    return tracks_of(title)[0]
