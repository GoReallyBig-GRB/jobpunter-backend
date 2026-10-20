"""Bounded, SSRF-resistant retrieval of user-submitted job posting URLs."""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.application.jobs.target_policy import ForbiddenTargetError, assert_target_not_forbidden
from app.domain.jobs.completeness import has_substantive_job_description


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
CONNECT_TIMEOUT_SECONDS = 4
READ_TIMEOUT_SECONDS = 6
USER_AGENT = "JobPunterJobPostingRetriever/1.0 (+user-submitted job URLs)"


class JobUrlRetrievalError(Exception):
    """A safe, non-content-bearing category for retrieval failures."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


class _PostingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.json_ld: list[dict] = []
        self._in_title = False
        self._script_type: str | None = None
        self._script_parts: list[str] = []
        self._description_parts: list[str] = []
        self._in_description = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            value = attributes.get("content", "").strip()
            if key and value:
                self.meta.setdefault(key, value)
        if tag == "script" and attributes.get("type", "").lower() == "application/ld+json":
            self._script_type = "application/ld+json"
            self._script_parts = []
        if tag in {"div", "section", "article"}:
            marker = " ".join([
                attributes.get("id", ""),
                attributes.get("class", ""),
                attributes.get("itemprop", ""),
            ]).lower()
            if any(token in marker for token in ("job-description", "job_description", "description")):
                self._in_description = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._script_type:
            try:
                parsed = json.loads("".join(self._script_parts))
                values = parsed if isinstance(parsed, list) else [parsed]
                for value in values:
                    if isinstance(value, dict):
                        graph = value.get("@graph")
                        if isinstance(graph, list):
                            self.json_ld.extend(item for item in graph if isinstance(item, dict))
                        else:
                            self.json_ld.append(value)
            except (json.JSONDecodeError, TypeError):
                pass
            self._script_type = None
            self._script_parts = []
        if tag in {"div", "section", "article"} and self._in_description:
            self._in_description = False

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
        if self._script_type:
            self._script_parts.append(data)
        if self._in_description:
            self._description_parts.append(value)


def _public_addresses(hostname: str, port: int) -> list[str]:
    try:
        answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise JobUrlRetrievalError("DNS_RESOLUTION_FAILED") from exc
    addresses = sorted({answer[4][0] for answer in answers})
    if not addresses:
        raise JobUrlRetrievalError("DNS_RESOLUTION_FAILED")
    # Reject the entire hostname if any DNS answer is non-public, preventing
    # mixed public/private DNS answers from being used as an SSRF escape hatch.
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exc:
            raise JobUrlRetrievalError("INVALID_DNS_ADDRESS") from exc
        if not parsed.is_global:
            raise JobUrlRetrievalError("NON_PUBLIC_ADDRESS")
    return addresses


def _fetch_once(url: str) -> tuple[int, dict[str, str], bytes]:
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise JobUrlRetrievalError("UNSUPPORTED_URL")
        if parts.username is not None or parts.password is not None:
            raise JobUrlRetrievalError("URL_CREDENTIALS_NOT_ALLOWED")
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
        if (
            (parts.scheme.lower() == "http" and port != 80)
            or (parts.scheme.lower() == "https" and port != 443)
        ):
            raise JobUrlRetrievalError("NON_STANDARD_PORT_NOT_ALLOWED")
        hostname = parts.hostname.encode("idna").decode("ascii")
        addresses = _public_addresses(hostname, port)
        sock = None
        last_error = None
        for address in addresses:
            try:
                sock = socket.create_connection((address, port), timeout=CONNECT_TIMEOUT_SECONDS)
                break
            except OSError as exc:
                last_error = exc
        if sock is None:
            raise JobUrlRetrievalError("CONNECTION_FAILED") from last_error
        sock.settimeout(READ_TIMEOUT_SECONDS)
        if parts.scheme.lower() == "https":
            try:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=hostname)
            except (OSError, ssl.SSLError) as exc:
                sock.close()
                raise JobUrlRetrievalError("TLS_VALIDATION_FAILED") from exc
        connection_type = http.client.HTTPSConnection if parts.scheme.lower() == "https" else http.client.HTTPConnection
        connection = connection_type(hostname, port=port, timeout=READ_TIMEOUT_SECONDS)
        connection.sock = sock
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        connection.request("GET", path, headers={
            "Host": parts.netloc,
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
            "Accept-Encoding": "identity",
            "Connection": "close",
        })
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        body = response.read(MAX_RESPONSE_BYTES + 1)
        connection.close()
        if len(body) > MAX_RESPONSE_BYTES:
            raise JobUrlRetrievalError("RESPONSE_TOO_LARGE")
        return response.status, headers, body
    except JobUrlRetrievalError:
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise JobUrlRetrievalError("CONNECTION_FAILED") from exc


def _job_posting_candidates(value: dict):
    kind = value.get("@type", [])
    kinds = {kind.lower()} if isinstance(kind, str) else {
        item.lower() for item in kind if isinstance(item, str)
    } if isinstance(kind, list) else set()
    if "jobposting" in kinds:
        yield value
    graph = value.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            if isinstance(item, dict):
                yield from _job_posting_candidates(item)


def _plain_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    return text or None


def _extract_posting(body: bytes, content_type: str) -> dict[str, str]:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
        raise JobUrlRetrievalError("UNSUPPORTED_CONTENT_TYPE")
    try:
        html = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise JobUrlRetrievalError("UNSUPPORTED_ENCODING") from exc
    if media_type == "text/plain":
        text = " ".join(html.split())
        if not has_substantive_job_description(text):
            raise JobUrlRetrievalError("POSTING_CONTENT_INCOMPLETE")
        return {"description": text[:40_000]}
    parser = _PostingParser()
    parser.feed(html)
    parser.close()
    structured = []
    for item in parser.json_ld:
        structured.extend(_job_posting_candidates(item))
    posting = structured[0] if structured else {}
    title = _plain_text(posting.get("title")) or _plain_text(
        parser.meta.get("og:title") or parser.meta.get("twitter:title")
    ) or _plain_text(" ".join(parser.title_parts))
    organization = posting.get("hiringOrganization")
    company = _plain_text(organization.get("name")) if isinstance(organization, dict) else None
    # A job board's site name is not evidence of the hiring organization.
    description = posting.get("description")
    if isinstance(description, str):
        # JobPosting descriptions often contain HTML fragments. Strip markup and
        # decode entities without interpreting the result as executable content.
        from html import unescape
        import re
        description = re.sub(r"<[^>]+>", " ", unescape(description))
    description = _plain_text(description) or _plain_text(
        parser.meta.get("og:description") or parser.meta.get("description")
    )
    location = None
    job_location = posting.get("jobLocation")
    if isinstance(job_location, dict):
        address = job_location.get("address")
        if isinstance(address, dict):
            location = ", ".join(
                value for value in (
                    _plain_text(address.get("addressLocality")),
                    _plain_text(address.get("addressRegion")),
                    _plain_text(address.get("addressCountry")),
                ) if value
            )
    application_url = _plain_text(posting.get("applicationUrl"))
    if (
        not title
        or not company
        or not description
        or not has_substantive_job_description(description)
    ):
        raise JobUrlRetrievalError("POSTING_CONTENT_INCOMPLETE")
    return {
        "title": title[:500],
        "company": company[:500],
        "description": description[:40_000],
        **({"location": location[:500]} if location else {}),
        **({"application_url": application_url[:4000]} if application_url else {}),
    }


def retrieve_job_posting(
    url: str,
    *,
    forbidden_target_rules: list[dict] | None = None,
) -> dict:
    """Fetch a user-submitted public HTTP(S) posting with strict network and content bounds."""
    current_url = url
    rules = forbidden_target_rules or []
    for redirect_count in range(MAX_REDIRECTS + 1):
        # Manual URL ingestion is an alternate acquisition entry path. Apply global
        # deny rules before every request, including each redirect destination.
        try:
            assert_target_not_forbidden(
                current_url, rules, connector_type="MANUAL_SUBMISSION"
            )
        except ForbiddenTargetError as exc:
            raise JobUrlRetrievalError("FORBIDDEN_TARGET_RULE_MATCHED") from exc
        except (TypeError, ValueError) as exc:
            raise JobUrlRetrievalError("TARGET_POLICY_INVALID") from exc
        status, headers, body = _fetch_once(current_url)
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location")
            if not location:
                raise JobUrlRetrievalError("REDIRECT_WITHOUT_LOCATION")
            if redirect_count >= MAX_REDIRECTS:
                raise JobUrlRetrievalError("TOO_MANY_REDIRECTS")
            next_url = urljoin(current_url, location)
            old_scheme = urlsplit(current_url).scheme.lower()
            new_scheme = urlsplit(next_url).scheme.lower()
            if old_scheme == "https" and new_scheme != "https":
                raise JobUrlRetrievalError("HTTPS_DOWNGRADE_BLOCKED")
            current_url = next_url
            continue
        if status < 200 or status >= 300:
            raise JobUrlRetrievalError(f"HTTP_STATUS_{status}")
        fields = _extract_posting(body, headers.get("content-type", ""))
        fields["retrieval_final_url"] = current_url
        fields["retrieval_method"] = "PUBLIC_HTTP_JOB_POSTING"
        fields["retrieved_at"] = datetime.now(timezone.utc).isoformat()
        return fields
    raise JobUrlRetrievalError("TOO_MANY_REDIRECTS")
