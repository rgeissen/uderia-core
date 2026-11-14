"""The ONE place an industry model's names come from.

A Databricks industry data model comes in two *scopes* — the repo's path
tokens are ``mvm`` (Minimum Viable Model) and ``ecm`` (Expanded Coverage
Model).  Those tokens are Databricks jargon: a demo audience reads
"minimum viable" as *unfinished* and "expanded coverage" as nothing at all.
So the scope keeps its repo token INTERNALLY (it is the path
``data-models/<industry>/v1/<scope>/model.json`` and cannot be renamed
upstream) and is rendered EVERYWHERE the platform names something after it
— the deployed database, the knowledge graph, the graph's root node and every
physical node's deployed identifier, the descriptor, the marketplace listing —
through the vocabulary below:

    mvm  ->  core   ("banking_core"  — the core banking model)
    ecm  ->  full   ("banking_full"  — the full banking model)

Every name-building site calls :func:`model_db_name`; none carries a copy of
the mapping.  A graph built before this vocabulary existed carries the legacy
``<industry>_<scope>`` name — :func:`legacy_db_name` recognises it so a
migration can find it, and nothing else ever emits it again.
"""
from __future__ import annotations

SCOPES = ("mvm", "ecm")                       # the repo tokens, in default order
SCOPE_TOKEN = {"mvm": "core", "ecm": "full"}  # repo token -> the name token
SCOPE_TITLE = {"mvm": "Core model", "ecm": "Full model"}
SCOPE_HINT = {
    "mvm": "the core model — the tables every implementation needs",
    "ecm": "the full model — expanded coverage of the industry",
}
DEFAULT_SCOPE = "mvm"


def normalize_scope(value) -> str:
    """The repo token for a scope given as a repo token OR a name token; default mvm."""
    s = (str(value or "").strip().lower()) or DEFAULT_SCOPE
    for repo, token in SCOPE_TOKEN.items():
        if s == token:
            return repo
    if s not in SCOPE_TOKEN:
        raise ValueError(f"model scope must be one of {sorted(SCOPE_TOKEN)} "
                         f"(or {sorted(SCOPE_TOKEN.values())}), got '{value}'")
    return s


def scope_token(flavour) -> str:
    return SCOPE_TOKEN[normalize_scope(flavour)]


def model_db_name(industry: str, flavour) -> str:
    """The name of a deployed model: the database, the graph and its root node."""
    return f"{(industry or '').strip()}_{scope_token(flavour)}"


def legacy_db_name(industry: str, flavour) -> str:
    """What the SAME model was called before the vocabulary (`<industry>_<repo token>`)."""
    return f"{(industry or '').strip()}_{normalize_scope(flavour)}"


def is_legacy_name(name: str, industry: str, flavour) -> bool:
    return (name or "").strip().lower() == legacy_db_name(industry, flavour).lower()
