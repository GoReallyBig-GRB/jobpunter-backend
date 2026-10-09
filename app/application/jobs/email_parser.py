from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any


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
    return len(re.findall(r"###|Role:|Position:|Job \d+:", content, re.IGNORECASE)) > 1


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
) -> dict[str, Any]:
    subject = raw_subject or ""
    content = raw_content or ""
    email_id = f"email_{uuid4().hex[:8]}"
    source = detect_email_source(subject, content, raw_sender)
    is_alert = is_job_alert_email(subject, content)
    multi_match = (
        re.search(r"(\d+)\s+(?:new\s+)?(?:jobs|openings|roles|product manager)", subject, re.IGNORECASE)
        or re.search(r"(\d+)\s+more\s+(?:product manager|remote|jobs)", content, re.IGNORECASE)
    )
    multiple_headings = has_multiple_headings(content)
    is_multi = bool(
        multi_match or multiple_headings
        or len(re.split(r"\n\s*---+\s*\n", content)) > 2
    )
    received = datetime.now(timezone.utc).isoformat()

    def source_email(status: str, count: int, notes: str) -> dict[str, Any]:
        return {
            "id": email_id,
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
        if "---" in content:
            chunks = [chunk for chunk in re.split(r"\n\s*---+\s*\n", content) if len(chunk.strip()) > 30]
        elif "###" in content:
            chunks = [chunk for chunk in content.split("###") if len(chunk.strip()) > 30]
        else:
            chunks = [chunk for chunk in re.split(r"\n\s*\n\s*\n", content) if len(chunk.strip()) > 30]
        jobs: list[dict[str, Any]] = []
        for chunk in chunks:
            parsed = extract_single_job_from_text(chunk, source)
            if parsed["title"] and (parsed["company"] or parsed["jobUrl"] or parsed["description"]):
                parsed["sourceEmailId"] = email_id
                jobs.append(parsed)
        if not jobs or (multi_match and len(jobs) == 1):
            return {
                "sourceEmail": source_email(
                    "PARSING_REQUIRED", 0,
                    f"Detected multi-job digest (e.g. '{subject}'). Cannot reliably separate individual listings without risking inaccurate scoring. Human parsing review required.",
                ),
                "detectedSource": source,
                "isJobAlert": True,
                "isMultiJobAlert": True,
                "extractedJobs": [],
                "parsingStatus": "PARSING_REQUIRED",
                "notes": "Digest contains multiple bundled opportunities that require manual inspection or delimited breakdown.",
            }
        return {
            "sourceEmail": source_email(
                "PROCESSED", len(jobs),
                f"Successfully separated {len(jobs)} individual job listings from multi-job digest.",
            ),
            "detectedSource": source,
            "isJobAlert": True,
            "isMultiJobAlert": True,
            "extractedJobs": jobs,
            "parsingStatus": "PROCESSED",
            "notes": f"Extracted {len(jobs)} distinct jobs from alert.",
        }

    job = extract_single_job_from_text(content, source, subject)
    job["sourceEmailId"] = email_id
    status = "PROCESSED" if job["title"] and job["company"] else "PARSING_REQUIRED"
    return {
        "sourceEmail": source_email(
            status, 1,
            "Single job opportunity extracted." if status == "PROCESSED"
            else "Missing essential fields (Title or Company). Marked PARSING REQUIRED.",
        ),
        "detectedSource": source,
        "isJobAlert": is_alert,
        "isMultiJobAlert": False,
        "extractedJobs": [job],
        "parsingStatus": status,
        "notes": "Clean single job extracted." if status == "PROCESSED" else "Low structure in single email body.",
    }
