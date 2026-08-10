"""
Centralized PII masking for fazle-mcp's own tool outputs — Python
counterpart to assistant-backend's backend/src/lib/piiMask.js. Deliberately
duplicated rather than shared across the two processes/languages (matches
this codebase's existing convention, e.g. the PERSONAS dict duplicated
between hermes-runner/server.py and backend/src/routes/hermes.js) — keep
the masking *algorithm* identical in both places if either changes.

Format follows AI_ROLES_POLICY.md's "Data Display Rules" (Owner-approved
2026-08-03): keep a leading country/trunk prefix + the last 4 digits,
mask everything in between. Admin context => never masked.

2026-08-10: PII_FIELDS drifted from piiMask.js's copy for a while (this
file had target_id/canonical_phone, the JS side didn't) despite this exact
docstring saying to keep them identical — now back in sync. Before editing
PII_FIELDS/TEXT_SCAN_FIELDS here, grep backend/src/lib/piiMask.js's same
two sets and update both together.
"""

import re

PII_FIELDS = {"whatsapp_number", "sender_number", "escort_mobile", "phone", "target_id", "canonical_phone"}

# Free-text fields that can carry a phone number typed by the sender rather
# than being a phone number themselves — field-name masking (PII_FIELDS)
# can't catch this. Kept identical to backend/src/lib/piiMask.js's
# TEXT_SCAN_FIELDS/PHONE_IN_TEXT_RE (found live 2026-08-04, see that file).
TEXT_SCAN_FIELDS = {"message_body"}
_PHONE_IN_TEXT_RE = re.compile(r"(?:\+?880|0)1[0-9]{9}\b")


def mask_phone(raw):
    """Mask a phone-shaped string, keeping a leading prefix + last 4 digits.

    Examples: "01712345678" -> "0XXXXXXX5678", "+8801712345678" -> "+880XXXXXXX5678".
    Falls through unchanged for values that don't look like a phone number
    (too short, or no digits) — callers only invoke this on known PII fields,
    but this stays safe if a field's value is unexpectedly empty/short.
    """
    if not raw or not isinstance(raw, str):
        return raw
    digits_only = re.sub(r"\D", "", raw)
    if len(digits_only) < 5:
        return raw

    if raw.startswith("+"):
        prefix = raw[:4]  # "+880" style
    elif raw.startswith("880") and len(digits_only) > 10:
        prefix = raw[:3]
    elif raw.startswith("0"):
        prefix = raw[:1]
    else:
        prefix = ""

    last4 = raw[-4:]
    middle_len = max(len(raw) - len(prefix) - 4, 0)
    return prefix + ("X" * middle_len) + last4


def mask_phones_in_text(text):
    """Mask any phone-shaped substring found within a larger block of free
    text, leaving the rest untouched — for fields where a phone number
    might just be *mentioned*, not be the whole field's value."""
    if not isinstance(text, str):
        return text
    return _PHONE_IN_TEXT_RE.sub(lambda m: mask_phone(m.group(0)), text)


def mask_pii(value, is_admin, fields=None, text_fields=None):
    """Recursively mask known PII fields in dicts/lists. No-op for admins.
    text_fields (default TEXT_SCAN_FIELDS) get scanned for embedded phone
    numbers rather than fully masked."""
    fields = fields or PII_FIELDS
    text_fields = text_fields if text_fields is not None else TEXT_SCAN_FIELDS
    if is_admin:
        return value
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in fields and isinstance(v, str):
                out[k] = mask_phone(v)
            elif k in text_fields and isinstance(v, str):
                out[k] = mask_phones_in_text(v)
            else:
                out[k] = mask_pii(v, is_admin, fields, text_fields)
        return out
    if isinstance(value, list):
        return [mask_pii(item, is_admin, fields, text_fields) for item in value]
    return value
