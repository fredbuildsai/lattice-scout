"""Notification templates and preferences. Mail copy never names a person, a paper or a decision."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Template(StrEnum):
    """The e-mails Scout can send (magic-link mails belong to Supabase Auth)."""

    RUN_FINISHED = "run_finished"
    MATCH_PROPOSED = "match_proposed"
    MATCH_MUTUAL = "match_mutual"
    WEEKLY_DIGEST = "weekly_digest"


@dataclass(frozen=True)
class Prefs:
    """What an account wants by e-mail. The digest is off by default."""

    email_runs: bool = True
    email_matches: bool = True
    email_digest: bool = False
    analytics_opt_out: bool = False  # drop this account's product-analytics events

    def allows(self, template: Template) -> bool:
        """Whether ``template`` may be sent."""
        return {
            Template.RUN_FINISHED: self.email_runs,
            Template.MATCH_PROPOSED: self.email_matches,
            Template.MATCH_MUTUAL: self.email_matches,
            Template.WEEKLY_DIGEST: self.email_digest,
        }[template]


@dataclass(frozen=True)
class Message:
    """A rendered e-mail (plain text)."""

    subject: str
    body: str


_COPY: dict[Template, tuple[str, str, str]] = {
    Template.RUN_FINISHED: (
        "Your Scout analysis is ready",
        "Your analysis has finished. Open it to see the research opportunities Scout found.",
        "/opportunities",
    ),
    Template.MATCH_PROPOSED: (
        "A new collaboration suggestion is waiting",
        "Scout has a new suggestion for you. It is anonymous until you both say yes. Open it to decide.",
        "/matches",
    ),
    Template.MATCH_MUTUAL: (
        "It's a match: you both said yes",
        "Both of you said yes. Open Scout to see the contact details and some joint ideas.",
        "/matches",
    ),
    Template.WEEKLY_DIGEST: (
        "Your weekly Scout summary",
        "Open Scout to see new opportunities and any collaboration suggestions that are waiting.",
        "/",
    ),
}

FOOTER = "You get this because of your notification settings. Change them under Settings, Privacy."


def render(template: Template, base_url: str) -> Message:
    """Subject and body for ``template``, with a link into the app."""
    subject, text, path = _COPY[template]
    return Message(subject, f"{text}\n\n{base_url.rstrip('/')}{path}\n\n{FOOTER}\n")
