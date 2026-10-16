from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from app.domain.jobs.completeness import has_substantive_job_description


_ALERT_KEYWORDS = (
    "job alert", "jobs alert", "new jobs for you", "recommended jobs",
    "jobs matching", "openings", "hiring now", "remote jobs", "product manager",
    "view job", "apply now", "new job posting", "opportunities for you",
)


def detect_email_source(subject: str, content: str, sender: str) -> str:
    combined = f"{subject} {content} {sender}".lower()
    for marker, source in (
        ("indeed", "Indeed"),
        ("linkedin", "LinkedIn"),
        ("jobberman", "Jobberman"),
        ("weworkremotely", "We Work Remotely"),
        ("we work remotely", "We Work Remotely"),
        ("wellfound", "Wellfound"),
        ("angel.co", "Wellfound"),
        ("greenhouse", "Greenhouse"),
        ("lever", "Lever"),
    ):
        if marker in combined:
            return source
    return "Direct Alert"


def is_job_alert_email(subject: str, content: str) -> bool:
    combined = f"{subject} {content}".lower()
    return any(keyword in combined for keyword in _ALERT_KEYWORDS)


def has_multiple_headings(content: str) -> bool:
    patterns = (
        r"^\s*#{1,6}\s*",
        r"^\s*(?:title|job title|role|position)\s*:",
        r"^\s*job\s*#?\d+\s*[:.)-]",
    )
    return sum(
        1
        for line in content.splitlines()
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns)
    ) > 1


def _split_multi_job_content(content: str) -> list[str]:
    """Split on explicit job boundaries or strongly job-shaped digest blocks."""
    text = content.strip()
    if not text:
        return []

    for pattern in (
        r"(?im)^[ \t]*-{3,}[ \t]*$",
        r"(?m)^[ \t]*#{2,6}[ \t]*$",
    ):
        chunks = [part.strip() for part in re.split(pattern, text) if part.strip()]
        if len(chunks) > 1:
            return chunks

    # Repeated structured title/role lines are reliable boundaries even when the
    # email template does not include horizontal rules between listings.
    title_pattern = re.compile(
        r"(?im)^[ \t]*(?:title|job title|role|position)[ \t]*:[ \t]*.+$"
    )
    title_matches = list(title_pattern.finditer(text))
    if len(title_matches) > 1:
        starts = [match.start() for match in title_matches]
        if starts[0] > 0 and text[:starts[0]].strip():
            starts.insert(0, 0)
        return [
            text[start:end].strip()
            for start, end in zip(starts, starts[1:] + [len(text)])
            if text[start:end].strip()
        ]

    numbered_pattern = re.compile(
        r"(?im)^[ \t]*(?:job|opportunity|opening)[ \t]*#?\d+[ \t]*[:.)-].*$"
    )
    numbered_matches = list(numbered_pattern.finditer(text))
    if len(numbered_matches) > 1:
        starts = [match.start() for match in numbered_matches]
        if starts[0] > 0 and text[:starts[0]].strip():
            starts.insert(0, 0)
        return [
            text[start:end].strip()
            for start, end in zip(starts, starts[1:] + [len(text)])
            if text[start:end].strip()
        ]

    # Many alert templates separate cards with blank lines. Split only when at
    # least two blocks independently look like job cards; preserve single-job
    # descriptions that use blank lines between paragraphs.
    blocks = [part.strip() for part in re.split(r"\n[ \t]*\n+", text) if part.strip()]
    candidates = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(block) < 80 or not lines:
            continue
        has_structured_field = bool(re.search(
            r"(?im)^[ \t]*(?:title|job title|role|position|company|employer)[ \t]*:", block
        ))
        has_url = bool(re.search(r"https?://[^\s)]+", block, re.IGNORECASE))
        first_lines_are_compact = len(lines[0]) <= 120 and (
            len(lines) > 1 and len(lines[1]) <= 120
        )
        if has_structured_field or has_url or first_lines_are_compact:
            candidates.append(block)
    if len(candidates) > 1:
        return candidates
    return [text]


def extract_single_job_from_text(
    text: str = "",
    default_source: str = "Direct",
    fallback_subject: str | None = None,
) -> dict[str, Any]:
    safe_text = text or ""
    lines = [line.strip() for line in safe_text.splitlines() if line.strip()]
    title = ""
    company = ""
    location = "Remote"
    job_url = ""

    for line in lines:
        if not title:
            match = re.search(r"(?:title|role|position):\s*(.+)", line, re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                continue
        if not company:
            match = re.search(r"(?:company|employer|organisation|organization):\s*(.+)", line, re.IGNORECASE)
            if match:
                company = match.group(1).strip()
                continue
        if location == "Remote":
            match = re.search(r"(?:location|geography|workplace):\s*(.+)", line, re.IGNORECASE)
            if match:
                location = match.group(1).strip()
                continue
        if not job_url:
            match = re.search(r"https?://[^\s)]+", line, re.IGNORECASE)
            if match:
                job_url = match.group(0).strip()
                continue

    if not title and lines:
        if re.search(r"product|manager|lead|owner", lines[0], re.IGNORECASE):
            title = re.sub(r"^#+\s*", "", lines[0]).strip()
        elif fallback_subject and re.search(r"product|manager|lead|owner", fallback_subject, re.IGNORECASE):
            match = re.search(r"(.+?)\s+(?:at|@)\s+(.+)", fallback_subject, re.IGNORECASE)
            if match:
                title, company = match.group(1).strip(), match.group(2).strip()
            else:
                title = re.sub(r"job alert:?", "", fallback_subject, flags=re.IGNORECASE).strip()

    if not company and len(lines) > 1:
        second = lines[1]
        if not re.search(r"https?:|/|requirements", second, re.IGNORECASE) and len(second) < 60:
            company = re.sub(r"^(?:at|@)\s*", "", second, flags=re.IGNORECASE).strip()

    return {
        "title": title,
        "company": company,
        "location": location,
        "source": default_source,
        "jobUrl": job_url,
        "description": safe_text.strip(),
    }


def parse_job_alert_email(
    raw_subject: str = "",
    raw_content: str = "",
    raw_sender: str = "alerts@jobplatform.com",
    source_message_id: str | None = None,
) -> dict[str, Any]:
    subject = raw_subject or ""
    content = raw_content or ""
    message_identity = (source_message_id or "").strip()
    if not message_identity:
        message_identity = "fingerprint:" + hashlib.sha256(
            f"{raw_sender.strip()}\x1f{subject}\x1f{content}".encode("utf-8")
        ).hexdigest()
    email_id = "email_" + hashlib.sha256(message_identity.encode("utf-8")).hexdigest()[:32]
    source = detect_email_source(subject, content, raw_sender)
    is_alert = is_job_alert_email(subject, content)
    multi_match = (
        re.search(r"(\d+)\s+(?:new\s+)?(?:jobs|openings|roles|product manager)", subject, re.IGNORECASE)
        or re.search(r"(\d+)\s+more\s+(?:product manager|remote|jobs)", content, re.IGNORECASE)
    )
    multiple_headings = has_multiple_headings(content)
    is_multi = bool(
        multi_match
        or multiple_headings
        or len(re.findall(
            r"(?im)^[ \t]*(?:title|job title|role|position)[ \t]*:",
            content,
        )) > 1
        or len(re.findall(
            r"(?im)^[ \t]*(?:job|opportunity|opening)[ \t]*#?\d+[ \t]*[:.)-]",
            content,
        )) > 1
        or len(re.split(r"\n\s*---+\s*\n", content)) > 1
    )
    received = datetime.now(timezone.utc).isoformat()

    def source_email(status: str, count: int, notes: str) -> dict[str, Any]:
        return {
            "id": email_id,
            "sourceMessageId": source_message_id.strip() if source_message_id and source_message_id.strip() else None,
            "source": source,
            "sender": raw_sender,
            "subject": subject,
            "receivedDate": received,
            "rawContent": content,
            "processedStatus": status,
            "extractedJobCount": count,
            "notes": notes,
        }

    if is_multi:
        chunks = [
            chunk for chunk in _split_multi_job_content(content)
            if len(chunk.strip()) > 30
        ]
        jobs: list[dict[str, Any]] = []
        review_chunks: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks):
            parsed = extract_single_job_from_text(chunk, source)
            if (
                parsed["title"]
                and parsed["company"]
                and has_substantive_job_description(parsed["description"])
            ):
                parsed["sourceEmailId"] = email_id
                parsed["sourceMessageId"] = source_message_id.strip() if source_message_id and source_message_id.strip() else None
                jobs.append(parsed)
            else:
                review_chunks.append({"chunk_index": index, "content": chunk.strip()})
        if not jobs:
            return {
                "sourceEmail": source_email(
                    "PARSING_REQUIRED", 0,
                    f"Detected multi-job digest (e.g. '{subject}'). No chunk had enough confidently extracted fields; human review is required.",
                ),
                "detectedSource": source,
                "isJobAlert": True,
                "isMultiJobAlert": True,
                "extractedJobs": [],
                "reviewRequiredChunks": review_chunks or [{"chunk_index": 0, "content": content}],
                "reviewRequiredCount": len(review_chunks) or 1,
                "parsingStatus": "PARSING_REQUIRED",
                "notes": "No complete job chunks could be separated confidently; content was retained for review.",
            }
        partial = bool(review_chunks)
        return {
            "sourceEmail": source_email(
                "PARTIAL_REVIEW" if partial else "PROCESSED",
                len(jobs),
                f"Extracted {len(jobs)} jobs; {len(review_chunks)} chunk(s) require review."
                if partial else f"Successfully separated {len(jobs)} individual job listings from multi-job digest.",
            ),
            "detectedSource": source,
            "isJobAlert": True,
            "isMultiJobAlert": True,
            "extractedJobs": jobs,
            "reviewRequiredChunks": review_chunks,
            "reviewRequiredCount": len(review_chunks),
            "parsingStatus": "PARTIALLY_PROCESSED" if partial else "PROCESSED",
            "notes": f"Extracted {len(jobs)} distinct jobs; ambiguous chunks remain available for review.",
        }

    job = extract_single_job_from_text(content, source, subject)
    job["sourceEmailId"] = email_id
    job["sourceMessageId"] = source_message_id.strip() if source_message_id and source_message_id.strip() else None
    complete = bool(
        job["title"]
        and job["company"]
        and has_substantive_job_description(job["description"])
    )
    status = "PROCESSED" if complete else "PARSING_REQUIRED"
    extracted_jobs = [job] if complete else []
    result = {
        "sourceEmail": source_email(
            status, len(extracted_jobs),
            "Single job opportunity extracted." if complete
            else "Missing essential fields (Title or Company). Marked PARSING REQUIRED.",
        ),
        "detectedSource": source,
        "isJobAlert": is_alert,
        "isMultiJobAlert": False,
        "extractedJobs": extracted_jobs,
        "parsingStatus": status,
        "notes": "Clean single job extracted." if complete else "Low structure in single email body.",
    }
    if not complete:
        result["reviewRequiredChunks"] = [{"chunk_index": 0, "content": content.strip()}] if content.strip() else []
        result["reviewRequiredCount"] = len(result["reviewRequiredChunks"])
    return result
