"""log_redaction.py -- a patient identifier in a log line is a pseudonym, not the identifier.

Measured (EXECUTED, docs/race/DATA_PRIVACY_AND_RESEARCH.md): a canary patient id appeared in log lines at INFO, WARNING and CRITICAL
from the engine, the governance system and the orchestrator. Logs are shipped and retained by tooling nobody here controls, so the
identifier does not belong in them.

`patient_tag(id)` is `pt:` plus 12 hex characters of HMAC-SHA256(salt, id). It is stable within a deployment that sets
`OBSERVE_LOG_SALT` (so an operator can compute the tag of a patient to find their lines, and two lines about one patient correlate)
and, with no salt set, stable within one process and different in the next (so a log alone cannot be joined across restarts). It is
a log tag, not a de-identification of data: 48 bits is enough to tell patients apart in a log and is not offered as an integrity or
privacy guarantee. Set `OBSERVE_LOG_RAW_PATIENT_IDS=1` to log the identifier itself while troubleshooting; the production profile
refuses to start with that set.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Any

__all__ = ["patient_tag", "raw_ids_enabled", "safe_error", "scrub"]

_PROCESS_SALT = secrets.token_bytes(32)


def raw_ids_enabled() -> bool:
    return os.environ.get("OBSERVE_LOG_RAW_PATIENT_IDS", "").strip().lower() in ("1", "true", "yes")


def patient_tag(patient_id: Any) -> str:
    """The text to put in a log line in place of a patient identifier. Never raises."""
    try:
        if patient_id is None or patient_id == "":
            return "pt:-"
        if raw_ids_enabled():
            return str(patient_id)
        configured = os.environ.get("OBSERVE_LOG_SALT", "")
        key = configured.encode("utf-8") if configured else _PROCESS_SALT
        return "pt:" + hmac.new(key, str(patient_id).encode("utf-8"), hashlib.sha256).hexdigest()[:12]
    except Exception:  # noqa: BLE001 - a logging helper must never be the reason something fails
        return "pt:?"


def scrub(text: Any, *patient_ids: Any) -> str:
    """`text` with every given patient identifier replaced by its tag. Never raises."""
    try:
        out = str(text)
        for pid in sorted({str(p) for p in patient_ids if p not in (None, "")}, key=len, reverse=True):
            out = out.replace(pid, patient_tag(pid))
        return out
    except Exception:  # noqa: BLE001
        return "?"


def safe_error(error: BaseException, *patient_ids: Any) -> str:
    """`Type: message` for a log line, with the identifiers of the patients in hand replaced by their tags (an exception that
    says "patient X is not patient Y" would otherwise put both in the log). Never raises."""
    try:
        return f"{type(error).__name__}: {scrub(error, *patient_ids)}"
    except Exception:  # noqa: BLE001
        return "error"
