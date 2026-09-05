from datetime import date
import unicodedata


def fetch_transaction_history(customer_id: str) -> dict:
    """Fetches the last 30 days of transaction history for AML review."""
    # Mock implementation
    return {"status": "success", "data": []}

def screen_against_sanctions(name: str, country: str) -> dict:
    """Checks customer details against global sanctions lists."""
    # Mock implementation
    return {"status": "clear", "match_score": 0.0}

def _normalize_name(value: str) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(unicodedata.normalize("NFC", value).casefold().split())
    return normalized if any(char.isalpha() for char in normalized) else ""


def _name_similarity(submitted: str, reference: str) -> float:
    """Return one minus Levenshtein edit distance divided by the longer length."""
    if not submitted or not reference:
        return 0.0
    if submitted == reference:
        return 1.0
    if len(submitted) < len(reference):
        submitted, reference = reference, submitted

    previous = list(range(len(reference) + 1))
    for row, left in enumerate(submitted, start=1):
        current = [row]
        for column, right in enumerate(reference, start=1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left != right),
            ))
        previous = current
    return 1.0 - previous[-1] / len(submitted)


def _parse_dob(value: str) -> date | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    # Enforce YYYY-MM-DD even on Python versions accepting other ISO formats.
    return parsed if parsed.isoformat() == value else None


def _normalize_id(value: str) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if any(char.isalnum() for char in value) else ""


def verify_identity_document(
    submitted_name: str,
    submitted_dob: str,
    submitted_id_number: str,
    reference_name: str,
    reference_dob: str,
    reference_id_number: str,
) -> dict:
    """Score submitted identity fields against caller-supplied trusted fields.

    Args:
        submitted_name: Name supplied on the identity document.
        submitted_dob: Document date of birth in YYYY-MM-DD format.
        submitted_id_number: Document ID number, supplied as a string.
        reference_name: Name from the trusted customer record.
        reference_dob: Reference date of birth in YYYY-MM-DD format.
        reference_id_number: Reference ID number, supplied as a string.

    Names use Unicode NFC, case folding, collapsed whitespace, and normalized
    Levenshtein similarity. Punctuation, accents, and name order are retained.
    DOBs must be real calendar dates in YYYY-MM-DD format and match exactly;
    no age or future-date rules are applied. IDs match exactly, preserving case,
    internal spacing, punctuation, and leading zeros. DOBs and IDs are stripped
    of surrounding whitespace only. A name must contain a letter; an ID must
    contain an alphanumeric character. Missing, non-string, or invalid values
    earn zero for that field, including when both values are empty or invalid.

    Returns a dictionary with status="success" (scoring completed), field_scores
    for name/dob/id_number, and match_confidence, all scores in [0, 1]. Name
    similarity and the total are rounded to four decimal places. The total is
    0.30 * name + 0.30 * dob + 0.40 * id_number; weights are never redistributed
    for missing fields. These are project heuristic weights, not a calibrated
    identity probability or a prescribed AML/KYC standard. No pass/fail decision,
    record lookup, persistence, document authenticity check, or logging occurs.

    Flexible name matching and strict ID matching are inspired by OFAC's search
    guidance (https://ofac.treasury.gov/faqs/249 and /faqs/253). This simplified
    algorithm does not implement OFAC's full sanctions matching methodology.
    """
    name_score = round(_name_similarity(
        _normalize_name(submitted_name), _normalize_name(reference_name)
    ), 4)
    document_dob = _parse_dob(submitted_dob)
    customer_dob = _parse_dob(reference_dob)
    dob_score = float(document_dob is not None and document_dob == customer_dob)
    document_id = _normalize_id(submitted_id_number)
    customer_id = _normalize_id(reference_id_number)
    id_score = float(bool(document_id) and document_id == customer_id)

    return {
        "status": "success",
        "match_confidence": round(0.30 * name_score + 0.30 * dob_score + 0.40 * id_score, 4),
        "field_scores": {"name": name_score, "dob": dob_score, "id_number": id_score},
    }


AML_TOOLS = [fetch_transaction_history, screen_against_sanctions, verify_identity_document]
