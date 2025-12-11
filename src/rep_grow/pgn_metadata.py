import re
from typing import Tuple

_REACH_COUNT_PATTERN = re.compile(r"\[rg:games=(\d+)\]")


def extract_reach_count(comment: str | None) -> Tuple[int | None, str]:
    """Return the reach count (if tagged) and the comment with tags removed."""

    if not comment:
        return None, ""
    matches = list(_REACH_COUNT_PATTERN.finditer(comment))
    if not matches:
        return None, comment
    count = int(matches[-1].group(1))
    cleaned = _REACH_COUNT_PATTERN.sub("", comment).strip()
    return count, cleaned


def upsert_reach_count_tag(comment: str | None, count: int) -> str:
    """Remove any existing reach-count tag and append a fresh one."""

    base = _REACH_COUNT_PATTERN.sub("", comment or "").strip()
    tag = f"[rg:games={count}]"
    if not base:
        return tag
    if base.endswith("\n"):
        return f"{base}{tag}"
    return f"{base} {tag}"
