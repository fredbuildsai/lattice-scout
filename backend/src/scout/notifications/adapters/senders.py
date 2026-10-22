"""E-mail senders: console (default, sends nothing) and Resend (needs EMAIL_API_KEY; never used from the VM)."""

from __future__ import annotations

import httpx
import structlog

from scout.shared import InfrastructureError

log = structlog.get_logger()


class ConsoleEmailSender:
    """Writes the mail to the log; nothing leaves the machine. The address is reduced to its domain."""

    async def send(self, to: str, subject: str, body: str) -> None:
        """Log subject and body (copy contains links only)."""
        log.info("email.console", to_domain=to.rsplit("@", 1)[-1], subject=subject, body=body)


class ResendEmailSender:
    """Resend REST API."""

    def __init__(self, http: httpx.AsyncClient, api_key: str, sender: str) -> None:
        self._http, self._key, self._from = http, api_key, sender

    async def send(self, to: str, subject: str, body: str) -> None:
        """POST /emails.

        Raises:
            InfrastructureError: network failure or a non-2xx answer.
        """
        try:
            r = await self._http.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self._key}"},
                json={"from": self._from, "to": [to], "subject": subject, "text": body},
                timeout=15,
            )
        except httpx.HTTPError as exc:
            raise InfrastructureError("E-mail provider unavailable") from exc
        if r.status_code >= 300:
            raise InfrastructureError(f"E-mail provider refused the mail ({r.status_code})")
