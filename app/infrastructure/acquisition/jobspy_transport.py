"""Guard JobSpy's outbound transports at the request and connection boundaries."""
from __future__ import annotations

import ipaddress
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import httpcore
from requests.adapters import HTTPAdapter
from urllib3 import connection, connectionpool
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.exceptions import NewConnectionError
from urllib3.poolmanager import PoolManager

from app.application.jobs.target_policy import (
    ForbiddenTargetError,
    assert_current_target_not_forbidden,
)
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome
from app.infrastructure.acquisition.public_http import _public_address

PolicyRevalidator = Callable[..., None]
_ACTIVE_GUARD: "_Guard | None" = None
_RUN_LOCK = threading.RLock()
_PATCH_LOCK = threading.Lock()
_PINNED = threading.local()


@dataclass
class _Guard:
    context: dict[str, Any]
    revalidator: PolicyRevalidator | None
    rules: list[dict[str, Any]]
    policy_lock: threading.Lock = field(default_factory=threading.Lock)


def _guard() -> _Guard:
    if _ACTIVE_GUARD is None:
        raise RuntimeError("JobSpy request occurred outside guarded acquisition")
    return _ACTIVE_GUARD


def _resolve_public(host: str, port: int) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return _public_address(host, port)
    except (httpcore.ConnectError, OSError, ValueError) as exc:
        raise AcquisitionAdapterError(
            AcquisitionOutcome.RESTRICTED, "JOBSPY_TARGET_NOT_PUBLIC"
        ) from exc


def _validate(url: str, method: str) -> tuple[str, int, str]:
    guard = _guard()
    parsed = urlsplit(url)
    method = method.upper()
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise AcquisitionAdapterError(AcquisitionOutcome.RESTRICTED, "JOBSPY_URL_INVALID") from exc
    if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or not 1 <= port <= 65535):
        raise AcquisitionAdapterError(AcquisitionOutcome.RESTRICTED, "JOBSPY_URL_INVALID")
    if method != "GET":
        raise AcquisitionAdapterError(AcquisitionOutcome.RESTRICTED, "ACQUISITION_METHOD_NOT_ALLOWED")
    host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    address = str(_resolve_public(host, port))
    try:
        with guard.policy_lock:
            assert_current_target_not_forbidden(
                url=url,
                context={
                    **guard.context,
                    "acquisition_policy_revalidator": guard.revalidator,
                    "forbidden_target_rules": guard.rules,
                },
                connector_type="JOBSPY",
                method=method,
            )
    except ForbiddenTargetError as exc:
        raise AcquisitionAdapterError(
            AcquisitionOutcome.RESTRICTED, "FORBIDDEN_TARGET_RULE_MATCHED"
        ) from exc
    return host, port, address


def _pinned_ip(host: str, port: int) -> str:
    address = getattr(_PINNED, "addresses", {}).get((host.lower().rstrip("."), port))
    if not address:
        raise NewConnectionError(None, "No validated public address is pinned")
    return address


class _PinnedHTTPConnection(HTTPConnection):
    def _new_conn(self):
        try:
            return connection.create_connection(
                (_pinned_ip(self.host, self.port), self.port), self.timeout,
                source_address=self.source_address, socket_options=self.socket_options,
            )
        except OSError as exc:
            raise NewConnectionError(self, "Failed to connect to validated public address") from exc


class _PinnedHTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        try:
            return connection.create_connection(
                (_pinned_ip(self.host, self.port), self.port), self.timeout,
                source_address=self.source_address, socket_options=self.socket_options,
            )
        except OSError as exc:
            raise NewConnectionError(self, "Failed to connect to validated public address") from exc


class _PinnedHTTPPool(connectionpool.HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection


class _PinnedHTTPSPool(connectionpool.HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


class _PinnedPoolManager(PoolManager):
    pool_classes_by_scheme = {"http": _PinnedHTTPPool, "https": _PinnedHTTPSPool}


class _GuardedHTTPAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        self._pool_connections, self._pool_maxsize, self._pool_block = connections, maxsize, block
        self.poolmanager = _PinnedPoolManager(
            num_pools=connections, maxsize=maxsize, block=block, **kwargs
        )

    def proxy_manager_for(self, proxy, **kwargs):
        raise RuntimeError("JobSpy proxy routing is not permitted")

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        if proxies and any(proxies.values()):
            raise RuntimeError("JobSpy proxy routing is not permitted")
        host, port, address = _validate(request.url, request.method)
        previous = getattr(_PINNED, "addresses", {})
        pins = dict(previous)
        pins[(host, port)] = address
        _PINNED.addresses = pins
        try:
            return super().send(
                request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies={}
            )
        finally:
            _PINNED.addresses = previous


def _redirect_method(method: str, status: int) -> str:
    if status == 303 and method != "HEAD":
        return "GET"
    if status in {301, 302} and method == "POST":
        return "GET"
    return method


class _GuardedTLSRotating:
    def request(self, method: str, url: str, *args: Any, **kwargs: Any):
        from curl_cffi import CurlOpt

        if getattr(self, "proxy_cycle", None) or kwargs.get("proxies") or kwargs.get("proxy"):
            raise RuntimeError("JobSpy proxy routing is not permitted")
        follow = kwargs.pop("allow_redirects", getattr(self, "allow_redirects", False))
        max_redirects = kwargs.pop("max_redirects", 10)
        if not isinstance(max_redirects, int) or not 0 <= max_redirects <= 10:
            raise RuntimeError("JobSpy redirect limit is invalid")
        current_method, current_url = method.upper(), url
        history = []
        headers = dict(kwargs["headers"]) if kwargs.get("headers") else None
        base_options = dict(kwargs.pop("curl_options", {}) or {})
        body_keys = ("data", "json", "content", "files")
        while True:
            host, port, address = _validate(current_url, current_method)
            ip = f"[{address}]" if ":" in address else address
            options = {**base_options, CurlOpt.RESOLVE: [f"{host}:{port}:{ip}"]}
            request_kwargs = {**kwargs, "allow_redirects": False, "curl_options": options}
            if headers is not None:
                request_kwargs["headers"] = headers
            response = super().request(current_method, current_url, *args, **request_kwargs)
            status = int(getattr(response, "status_code", 0))
            location = getattr(response, "headers", {}).get("location")
            if not follow or status not in {301, 302, 303, 307, 308} or not location:
                try:
                    response.history = history
                except (AttributeError, TypeError):
                    pass
                return response
            if len(history) >= max_redirects:
                raise RuntimeError("JobSpy exceeded guarded redirect limit")
            history.append(response)
            next_url = urljoin(current_url, location)
            old, new = urlsplit(current_url), urlsplit(next_url)
            if new.scheme.lower() not in {"http", "https"} or not new.hostname:
                raise RuntimeError("JobSpy redirect target is not an allowed HTTP(S) URL")
            if (old.scheme.lower(), old.hostname, old.port) != (new.scheme.lower(), new.hostname, new.port):
                if headers is not None:
                    headers = {k: v for k, v in headers.items()
                               if k.lower() not in {"authorization", "proxy-authorization", "cookie"}}
            current_method = _redirect_method(current_method, status)
            if current_method == "GET":
                for key in body_keys:
                    kwargs.pop(key, None)
            current_url = next_url


@contextmanager
def guarded_jobspy_policy(context: dict[str, Any]):
    global _ACTIVE_GUARD
    rules = context.get("forbidden_target_rules", [])
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise RuntimeError("JobSpy forbidden-target policy is invalid")
    revalidator = context.get("acquisition_policy_revalidator")
    if revalidator is not None and not callable(revalidator):
        raise RuntimeError("JobSpy live policy revalidator is invalid")
    active = _Guard(dict(context), revalidator, rules)
    with _RUN_LOCK:
        previous, _ACTIVE_GUARD = _ACTIVE_GUARD, active
        try:
            yield
        finally:
            _ACTIVE_GUARD = previous


def install_jobspy_transport() -> None:
    """Patch only JobSpy's shared session class references; never patch global sockets."""
    with _PATCH_LOCK:
        import jobspy.util as util

        if getattr(util, "_jobpunter_guarded_transport_installed", False):
            return
        original_requests, original_tls = util.RequestsRotating, util.TLSRotating

        class GuardedRequestsRotating(original_requests):
            def __init__(self, *args: Any, **kwargs: Any):
                super().__init__(*args, **kwargs)
                self.trust_env = False
                self.mount("http://", _GuardedHTTPAdapter())
                self.mount("https://", _GuardedHTTPAdapter())

        class GuardedTLSRotating(_GuardedTLSRotating, original_tls):
            pass

        for name, module in tuple(sys.modules.items()):
            if name.startswith("jobspy") and module is not None:
                for attr, value in tuple(vars(module).items()):
                    if value is original_requests:
                        setattr(module, attr, GuardedRequestsRotating)
                    elif value is original_tls:
                        setattr(module, attr, GuardedTLSRotating)
        util.RequestsRotating = GuardedRequestsRotating
        util.TLSRotating = GuardedTLSRotating
        util._jobpunter_guarded_transport_installed = True
