"""
The pane catalogue — the ONE list of the sidebar's views, as Pane Visibility shows them.

The ``pane_visibility`` table stores only what an admin decides (which tiers see a
view). Everything a person READS — the name, the sidebar group, the description, the
order — comes from this catalogue and is written onto the stored rows on every read
(``sync_panes``), so a renamed view can never linger under its old name in the
admin table again (the table once showed Setup / Conversations / Executions years
after the sidebar became Anatomy / Reasoning / Awareness, and it had no row at all
for Trust).

The order and the groups mirror ``templates/index.html`` (``#app-side-nav``):
Reasoning, then THE AGENT, the FACULTIES, the EXCHANGE and the PLATFORM. When the
sidebar gains, loses or renames a view, change it here in the same edit.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("quart.app")

# ``pane_id`` is a stored key (and the frontend's paneMap key) — never rename one;
# change ``pane_name`` instead. ``requires`` names an optional feature that must be
# present for the view to exist at all.
PANE_CATALOG: list[dict] = [
    {'pane_id': 'conversation', 'pane_name': 'Reasoning', 'group': None,
     'description': 'Conversations with your agents',
     'defaults': (True, True, True)},
    {'pane_id': 'credentials', 'pane_name': 'Anatomy', 'group': 'The Agent',
     'description': 'Agent profiles, LLMs, MCP servers, stores, memory, skills and packs',
     'defaults': (True, True, True)},
    {'pane_id': 'agents', 'pane_name': 'Trust', 'group': 'The Agent',
     'description': 'The trust circle, posture and assurance',
     'defaults': (True, True, True)},
    {'pane_id': 'executions', 'pane_name': 'Awareness', 'group': 'The Agent',
     'description': 'Sessions and performance',
     'defaults': (False, True, True)},
    {'pane_id': 'ontology', 'pane_name': 'Ontology', 'group': 'The Agent',
     'description': 'What your agents know, who they answer for, and what to trust',
     'defaults': (True, True, True), 'requires': 'ontology'},
    {'pane_id': 'rag-maintenance', 'pane_name': 'Intelligence', 'group': 'Faculties',
     'description': 'Knowledge, planners, graphs and skills',
     'defaults': (False, True, True)},
    {'pane_id': 'components', 'pane_name': 'Components', 'group': 'Faculties',
     'description': "The agent's instruments and connectors",
     'defaults': (False, True, True)},
    {'pane_id': 'marketplace', 'pane_name': 'Marketplace', 'group': 'Exchange',
     'description': 'Share and subscribe',
     'defaults': (True, True, True)},
    {'pane_id': 'admin', 'pane_name': 'Administration', 'group': 'Platform',
     'description': 'Users, governance and operations',
     'defaults': (False, False, True)},
]


def _feature_present(name: str) -> bool:
    if name == 'ontology':
        try:
            from trusted_data_agent.core.config import APP_STATE
            return bool(APP_STATE.get('ontology_enabled'))
        except Exception:
            return False
    return False


def active_catalog() -> list[dict]:
    """The catalogue entries whose view exists in this deployment, in sidebar order."""
    return [e for e in PANE_CATALOG if not e.get('requires') or _feature_present(e['requires'])]


def pane_dict(row, entry: dict | None = None) -> dict:
    """A stored row as the API returns it — the catalogue's ``group`` included."""
    if entry is None:
        entry = next((e for e in PANE_CATALOG if e['pane_id'] == row.pane_id), {})
    return {**row.to_dict(), 'group': entry.get('group')}


def sync_panes(session) -> list[dict]:
    """Bring the stored rows in line with the catalogue and return them in sidebar order.

    Missing views are inserted with their default visibility; existing rows keep the
    admin's visibility flags and take the catalogue's name, description and order. Rows
    for views that no longer exist (or whose optional feature is absent) are left in
    the table — a feature switched back on finds its admin's choice — but not returned.
    """
    from trusted_data_agent.auth.models import PaneVisibility

    rows = {p.pane_id: p for p in session.query(PaneVisibility).all()}
    out = []
    changed = False
    for order, entry in enumerate(active_catalog(), start=1):
        row = rows.get(entry['pane_id'])
        if row is None:
            user, dev, admin = entry['defaults']
            row = PaneVisibility(
                pane_id=entry['pane_id'], pane_name=entry['pane_name'],
                description=entry['description'], display_order=order,
                visible_to_user=user, visible_to_developer=dev, visible_to_admin=admin,
            )
            session.add(row)
            changed = True
        else:
            for attr, value in (('pane_name', entry['pane_name']),
                                ('description', entry['description']),
                                ('display_order', order)):
                if getattr(row, attr) != value:
                    setattr(row, attr, value)
                    changed = True
        if row.pane_id == 'admin' and not row.visible_to_admin:
            row.visible_to_admin = True
            changed = True
        out.append((row, entry))
    if changed:
        session.commit()
        logger.info("[PaneVisibility] pane catalogue synced onto stored rows")
    return [pane_dict(row, entry) for row, entry in out]


def reset_panes(session) -> list[dict]:
    """Drop every stored row and recreate the catalogue with its default visibility."""
    from trusted_data_agent.auth.models import PaneVisibility

    session.query(PaneVisibility).delete()
    session.commit()
    return sync_panes(session)
