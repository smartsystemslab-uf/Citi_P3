from datetime import date
import unicodedata


def _normalize_name(value: str) -> str:
    if not isinstance(value, str):
        return ""

    normalized = " ".join(
        unicodedata.normalize("NFC", value).casefold().split()
    )

    return normalized if any(char.isalpha() for char in normalized) else ""


def _name_similarity(submitted: str, reference: str) -> float:
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
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left != right),
                )
            )

        previous = current

    return 1.0 - previous[-1] / len(submitted)


def score_identity(
    submitted_name: str,
    submitted_dob: str,
    submitted_id_number: str,
    reference_name: str,
    reference_dob: str,
    reference_id_number: str,
) -> dict:
    name_score = round(
        _name_similarity(
            _normalize_name(submitted_name),
            _normalize_name(reference_name),
        ),
        4,
    )

    try:
        submitted_date = date.fromisoformat(submitted_dob.strip())
        reference_date = date.fromisoformat(reference_dob.strip())
        dob_score = float(submitted_date == reference_date)
    except (ValueError, AttributeError):
        dob_score = 0.0

    submitted_id = submitted_id_number.strip() if isinstance(submitted_id_number, str) else ""
    reference_id = reference_id_number.strip() if isinstance(reference_id_number, str) else ""

    id_score = float(bool(submitted_id) and submitted_id == reference_id)

    return {
        "status": "success",
        "match_confidence": round(
            0.30 * name_score
            + 0.30 * dob_score
            + 0.40 * id_score,
            4,
        ),
        "field_scores": {
            "name": name_score,
            "dob": dob_score,
            "id_number": id_score,
        },
    }