import re

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*```")


def _clamp_heading_line(line: str, max_level: int) -> str:
    match = _HEADING_RE.match(line)
    if not match:
        return line
    _, title = match.groups()
    return f"{'#' * max_level} {title}"


def normalize_markdown_headings(text: str, max_level: int = 4) -> str:
    if not text:
        return text

    lines = text.splitlines()
    normalized: list[str] = []
    in_fenced_code_block = False

    for line in lines:
        if _FENCE_RE.match(line):
            in_fenced_code_block = not in_fenced_code_block
            normalized.append(line)
            continue

        if in_fenced_code_block:
            normalized.append(line)
            continue

        normalized.append(_clamp_heading_line(line, max_level))

    return "\n".join(normalized)
