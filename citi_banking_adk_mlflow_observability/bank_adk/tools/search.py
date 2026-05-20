from collections import Counter

def rank_records(query: str, rows: list[dict], text_keys: list[str], top_k: int = 3) -> list[dict]:
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        return rows[:top_k]
    scored = []
    for row in rows:
        text = " ".join(str(row.get(k, "")) for k in text_keys).lower()
        counter = Counter(text.split())
        score = sum(counter.get(term, 0) + (2 if term in text else 0) for term in terms)
        scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for score, row in scored if score > 0][:top_k] or rows[:top_k]
