"""match_reveal_v: first publication year and joint works count of the other side

Both numbers come from the claimed (public OpenAlex) works that each researcher kept included. They exist only in
this view, so they inherit its rule: rows for mutual matches only.

Revision ID: 0018
Revises: 0017
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_BASE = """
SELECT mp.id AS match_id, v.viewer_id, v.other_id, r.display_name, r.institution, r.orcid, acc.email,
       ARRAY(SELECT x.openalex_author_id FROM researcher_openalex_id x
             WHERE x.researcher_id = r.id ORDER BY x.position) AS openalex_ids
"""
_EXTRA = """,
       (SELECT min(w.year) FROM claimed_work w
         WHERE w.researcher_id = r.id AND w.included AND w.year IS NOT NULL) AS career_since,
       (SELECT count(*) FROM claimed_work w1
          JOIN researcher rv ON rv.id = w1.researcher_id AND rv.account_id = v.viewer_id
          JOIN claimed_work w2 ON w2.openalex_work_id = w1.openalex_work_id AND w2.researcher_id = r.id
         WHERE w1.included AND w2.included) AS joint_works_count
"""
_TAIL = """
FROM match_proposal mp
CROSS JOIN LATERAL (VALUES (mp.a_id, mp.b_id), (mp.b_id, mp.a_id)) AS v(viewer_id, other_id)
JOIN account acc ON acc.id = v.other_id AND acc.deleted_at IS NULL
JOIN researcher r ON r.account_id = v.other_id
WHERE mp.state = 'mutual'
"""


def upgrade() -> None:
    op.execute(f"CREATE OR REPLACE VIEW match_reveal_v WITH (security_invoker = true) AS {_BASE}{_EXTRA}{_TAIL}")


def downgrade() -> None:
    op.execute("DROP VIEW match_reveal_v")
    op.execute(f"CREATE VIEW match_reveal_v WITH (security_invoker = true) AS {_BASE}{_TAIL}")
