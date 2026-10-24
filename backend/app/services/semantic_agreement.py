"""Shared semantic gate for community matching and official corroboration."""

EMBEDDING_REJECT_BELOW = 0.60
EMBEDDING_AUTO_MIN = 0.70


def embedding_gate(similarity, *, anchored=False):
    if similarity is None:
        return "missing"
    if similarity < EMBEDDING_REJECT_BELOW:
        return "separate"
    if similarity < EMBEDDING_AUTO_MIN and not anchored:
        return "review"
    return "pass"
