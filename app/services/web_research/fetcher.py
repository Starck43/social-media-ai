"""Safe public page fetcher, robots.txt checker, sanitizer, and read-only Chromium engine (P20-T12)."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.core.outbound import (
    ADDRESS_NOT_AUTHORIZED,
    DNS_RESOLUTION_FAILED,
    HOST_NOT_TRUSTED,
    INVALID_TARGET,
    PRIVATE_ADDRESS_FORBIDDEN,
    OutboundRefusal,
    OutboundTarget,
    RegistryReader,
    Resolver,
    StaticPolicy,
    acquire_outbound_target,
    default_dns_resolver,
    empty_registry,
)
from app.services.web_research.contracts import (
    ALLOWED_CONTENT_TYPES,
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_TIMEOUT_SECONDS,
    STUB_SEARCH_MARKER,
    UNTRUSTED_PAGE_MARKER,
    FetchModeKind,
    PageSnapshotOut,
    SearchHitOut,
    SearchProviderKind,
    WebResearchRefusal,
)

USER_AGENT = "SocialMediaAI-ResearchBot/1.0 (+https://social-media-ai.local/research; read-only)"

HARDENED_CHROMIUM_ARGS: tuple[str, ...] = (
    "--headless=new",
    "--incognito",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-plugins",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-translate",
    "--disable-default-apps",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--disk-cache-size=0",
    "--block-new-web-contents",
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_PORTS = frozenset({80, 443})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(
            r"(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:previous|prior|above|system)\s+"
            r"(?:instructions?|prompts?|rules?)",
            re.IGNORECASE,
        ),
    ),
    (
        "ru_ignore_instructions",
        re.compile(
            r"(?:игнорируй|забудь|отмени)\s+(?:все\s+)?(?:предыдущие|системные|вышеуказанные)\s+"
            r"(?:инструкции|правила|указания)",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_override",
        re.compile(
            r"(?:system\s*prompt\s*:|<\s*\|\s*im_start\s*\|\s*>\s*system|\[\s*INST\s*\]|###\s*System\s*:)",
            re.IGNORECASE,
        ),
    ),
    (
        "exfiltrate_or_tool_injection",
        re.compile(
            r"(?:выполни\s+команду|отправь\s+(?:секрет|токен|ключ)|exfiltrate|call\s+tool\s+`?\w+`?|tool_call\s*:)",
            re.IGNORECASE,
        ),
    ),
)

_CAPTCHA_OR_LOGIN_RE = re.compile(
    r"(?:g-recaptcha|cf-turnstile|hcaptcha|captcha-delivery|verify\s+you\s+are\s+human|"
    r"подтвердите,?\s+что\s+вы\s+не\s+робот|введите\s+капчу)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RawHttpResponse:
    """Normalized HTTP response returned by an injectable transport."""

    status_code: int
    headers: dict[str, str]
    body: bytes
    final_url: str = ""


HttpTransport = Callable[[str, float, int], Awaitable[RawHttpResponse]]


def _map_outbound_refusal(
    exc: OutboundRefusal, *, is_redirect: bool = False, rebinding: bool = False
) -> WebResearchRefusal:
    if rebinding:
        return WebResearchRefusal("dns_rebinding_detected", host=exc.host)
    if is_redirect:
        return WebResearchRefusal("redirect_blocked", host=exc.host)
    if exc.code in {PRIVATE_ADDRESS_FORBIDDEN, ADDRESS_NOT_AUTHORIZED}:
        return WebResearchRefusal("private_address_forbidden", host=exc.host)
    if exc.code == INVALID_TARGET:
        return WebResearchRefusal("invalid_url", host=exc.host)
    if exc.code in {HOST_NOT_TRUSTED, DNS_RESOLUTION_FAILED}:
        return WebResearchRefusal("ssrf_blocked", host=exc.host)
    return WebResearchRefusal("ssrf_blocked", host=exc.host)


def validate_public_url(
    raw_url: str,
    *,
    policy: StaticPolicy = StaticPolicy(),
    registry: RegistryReader = empty_registry,
    resolver: Resolver = default_dns_resolver,
    seen_resolutions: dict[str, set[str]] | None = None,
    is_redirect: bool = False,
) -> tuple[str, OutboundTarget]:
    """Validate URL syntax, scheme, port, and destination IP safety via `acquire_outbound_target`."""
    if not isinstance(raw_url, str) or not raw_url.strip() or len(raw_url) > 2000:
        raise WebResearchRefusal("redirect_blocked" if is_redirect else "invalid_url")
    trimmed = raw_url.strip()
    if any(ord(ch) <= 32 or ord(ch) == 127 for ch in trimmed):
        raise WebResearchRefusal("redirect_blocked" if is_redirect else "invalid_url")

    try:
        parts = urlsplit(trimmed)
    except ValueError:
        raise WebResearchRefusal("redirect_blocked" if is_redirect else "invalid_url") from None

    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise WebResearchRefusal("redirect_blocked" if is_redirect else "invalid_url")
    if parts.username is not None or parts.password is not None:
        raise WebResearchRefusal("redirect_blocked" if is_redirect else "invalid_url")

    host = parts.hostname or ""
    if not host:
        raise WebResearchRefusal("redirect_blocked" if is_redirect else "invalid_url")

    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise WebResearchRefusal("redirect_blocked" if is_redirect else "invalid_url") from None

    if port not in _ALLOWED_PORTS:
        raise WebResearchRefusal("redirect_blocked" if is_redirect else "invalid_url")

    try:
        literal_ip = ipaddress.ip_address(host.strip("[]"))
        if (
            not literal_ip.is_global
            or literal_ip.is_private
            or literal_ip.is_loopback
            or literal_ip.is_link_local
            or literal_ip.is_multicast
            or literal_ip.is_reserved
            or getattr(literal_ip, "ipv4_mapped", None) is not None
        ):
            raise WebResearchRefusal("redirect_blocked" if is_redirect else "private_address_forbidden", host=host)
    except ValueError:
        pass

    already_seen = seen_resolutions is not None and host.lower() in seen_resolutions
    try:
        target = acquire_outbound_target(
            host,
            port,
            policy=policy,
            registry=registry,
            resolver=resolver,
        )
    except OutboundRefusal as exc:
        raise _map_outbound_refusal(exc, is_redirect=is_redirect, rebinding=already_seen) from None

    if seen_resolutions is not None:
        key = target.host.lower()
        prev = seen_resolutions.get(key)
        current_set = set(target.addresses)
        if prev is not None and prev != current_set:
            raise WebResearchRefusal("dns_rebinding_detected", host=target.host)
        seen_resolutions[key] = current_set

    normalized_path = parts.path or "/"
    # Preserve the caller's explicit port, including scheme-default ports. Bracket
    # literal IPv6 addresses before putting them back into a URL authority.
    authority_host = f"[{target.host}]" if ":" in target.host else target.host
    explicit_port = parts.port is not None
    canonical_netloc = f"{authority_host}:{port}" if explicit_port else authority_host
    canonical_url = urlunsplit((scheme, canonical_netloc, normalized_path, parts.query, ""))
    return canonical_url, target


def is_allowed_by_robots(robots_txt: str, path: str, *, user_agent: str = "SocialMediaAI-ResearchBot") -> bool:
    """Evaluate standard robots.txt rules for `user_agent` and `*`."""
    if not robots_txt or not robots_txt.strip():
        return True
    target_path = path or "/"
    ua_token = user_agent.lower().split("/")[0].strip()

    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    current_agents: list[str] = []
    current_rules: list[tuple[str, str]] = []

    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field_name, value = line.split(":", 1)
        key = field_name.strip().lower()
        val = value.strip()
        if key == "user-agent":
            if current_rules:
                groups.append((current_agents, current_rules))
                current_agents = []
                current_rules = []
            current_agents.append(val.lower())
        elif key in {"allow", "disallow"} and current_agents:
            current_rules.append((key, val))

    if current_agents:
        groups.append((current_agents, current_rules))

    matched_rules: list[tuple[str, str]] = []
    wildcard_rules: list[tuple[str, str]] = []
    for agents, rules in groups:
        if any(ua_token in ag or ag in ua_token for ag in agents if ag != "*"):
            matched_rules.extend(rules)
        elif "*" in agents:
            wildcard_rules.extend(rules)

    active_rules = matched_rules if matched_rules else wildcard_rules
    if not active_rules:
        return True

    best_match_len = -1
    best_decision = True
    for directive, pattern in active_rules:
        if not pattern:
            if directive == "disallow":
                continue
        regex_parts: list[str] = ["^"]
        for ch in pattern:
            if ch == "*":
                regex_parts.append(".*")
            elif ch == "$":
                regex_parts.append("$")
            else:
                regex_parts.append(re.escape(ch))
        compiled = re.compile("".join(regex_parts))
        if compiled.search(target_path):
            specificity = len(pattern.replace("*", ""))
            if specificity > best_match_len or (specificity == best_match_len and directive == "allow"):
                best_match_len = specificity
                best_decision = directive == "allow"
    return best_decision


class _SafeTextHTMLParser(HTMLParser):
    """Extract visible text and title while stripping scripts, styles, forms, and hidden nodes."""

    _SKIP_TAGS = frozenset(
        {"script", "style", "noscript", "iframe", "object", "embed", "svg", "canvas", "form", "template"}
    )
    _BLOCK_TAGS = frozenset(
        {
            "p",
            "div",
            "section",
            "article",
            "main",
            "header",
            "footer",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "tr",
            "br",
        }
    )

    _VOID_TAGS = frozenset(
        {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_stack: list[str] = []
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.hidden_text_parts: list[str] = []
        self.has_password_input = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        attr_map = {k.lower(): (v or "").lower() for k, v in attrs if k}
        if lower == "input" and attr_map.get("type") == "password":
            self.has_password_input = True
        style = attr_map.get("style", "")
        is_hidden = (
            "hidden" in attr_map
            or attr_map.get("aria-hidden") == "true"
            or "display:none" in style.replace(" ", "")
            or "visibility:hidden" in style.replace(" ", "")
        )
        if self._skip_stack or lower in self._SKIP_TAGS or is_hidden:
            if lower not in self._VOID_TAGS:
                self._skip_stack.append(lower)
            return
        if lower == "title":
            self._in_title = True
        elif lower in self._BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = False
        if self._skip_stack:
            if self._skip_stack[-1] == lower:
                self._skip_stack.pop()
            elif lower in self._skip_stack:
                while self._skip_stack and self._skip_stack[-1] != lower:
                    self._skip_stack.pop()
                if self._skip_stack:
                    self._skip_stack.pop()
            return
        if lower in self._BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            if self._skip_stack[0] not in self._SKIP_TAGS:
                self.hidden_text_parts.append(data)
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.text_parts.append(data)


def sanitize_and_extract_page(
    raw_text: str,
    *,
    content_type: str = "text/html",
    fallback_title: str = "",
) -> dict[str, Any]:
    """Sanitize untrusted HTML/text, refuse CAPTCHA/login walls, and neutralize prompt injection."""
    if _CAPTCHA_OR_LOGIN_RE.search(raw_text):
        raise WebResearchRefusal("login_or_captcha_blocked")

    ctype = (content_type or "text/html").split(";", 1)[0].strip().lower()
    if ctype not in ALLOWED_CONTENT_TYPES:
        raise WebResearchRefusal("unsupported_content_type")

    hidden_text = ""
    if ctype == "text/plain":
        title = fallback_title or "Текстовый документ"
        visible_text = raw_text
    else:
        parser = _SafeTextHTMLParser()
        parser.feed(raw_text)
        parser.close()
        title = " ".join("".join(parser.title_parts).split()).strip() or fallback_title or "Публичная страница"
        visible_text = "".join(parser.text_parts)
        hidden_text = " ".join(parser.hidden_text_parts)
        if parser.has_password_input:
            raise WebResearchRefusal("login_or_captcha_blocked")

    cleaned_lines: list[str] = []
    injection_markers: list[str] = []
    if hidden_text:
        for code, pattern in _PROMPT_INJECTION_PATTERNS:
            if pattern.search(hidden_text) and code not in injection_markers:
                injection_markers.append(code)
    for raw_line in visible_text.splitlines():
        line = " ".join(raw_line.split()).strip()
        if not line:
            continue
        matched_codes: list[str] = []
        for code, pattern in _PROMPT_INJECTION_PATTERNS:
            if pattern.search(line):
                matched_codes.append(code)
                if code not in injection_markers:
                    injection_markers.append(code)
        if matched_codes:
            cleaned_lines.append("[ДИРЕКТИВА ВНЕШНЕЙ СТРАНИЦЫ ОТФИЛЬТРОВАНА]")
        else:
            cleaned_lines.append(line)

    normalized_text = "\n".join(cleaned_lines).strip()
    excerpt = normalized_text[:600]
    untrusted_body = f"{UNTRUSTED_PAGE_MARKER}\n{normalized_text[:8000]}"
    content_hash = "sha256:" + hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()

    return {
        "title": title[:300],
        "clean_text": normalized_text,
        "excerpt": excerpt,
        "untrusted_body": untrusted_body,
        "content_hash": content_hash,
        "prompt_injection_detected": bool(injection_markers),
        "injection_markers": injection_markers,
    }


def _build_pinned_request(client: Any, url: str, target: OutboundTarget) -> Any:
    """Build an HTTPX request whose TCP peer is a previously validated IP."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not target.addresses or target.host.lower() != host.lower().rstrip("."):
        raise WebResearchRefusal("ssrf_blocked")
    try:
        tls_host = host.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        raise WebResearchRefusal("invalid_url") from None
    port = parts.port
    default_port = 443 if parts.scheme == "https" else 80
    if target.port != (port or default_port):
        raise WebResearchRefusal("ssrf_blocked")
    host_header = f"[{tls_host}]" if ":" in tls_host else tls_host
    if port is not None and port != default_port:
        host_header = f"{host_header}:{port}"
    address = target.addresses[0]
    connection_host = f"[{address}]" if ":" in address else address
    authority = f"{connection_host}:{port or default_port}"
    request_url = urlunsplit((parts.scheme, authority, parts.path or "/", parts.query, ""))
    request = client.build_request(
        "GET",
        request_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,text/plain,application/xhtml+xml",
            "Host": host_header,
        },
    )
    if parts.scheme == "https":
        request.extensions["sni_hostname"] = tls_host.encode("ascii")
    return request


class ChromiumUnderHoodEngine:
    """Default read-only Chromium search & page-render engine when Yandex Search API is not configured."""

    def __init__(
        self,
        *,
        binary: str = "chromium",
        search_runner: Callable[[str, int], Awaitable[list[dict[str, str]]]] | None = None,
        page_runner: Callable[[str, float, int], Awaitable[RawHttpResponse]] | None = None,
    ) -> None:
        self.binary = binary
        self._search_runner = search_runner
        self._page_runner = page_runner

    def command_args_for_url(self, url: str, *, resolver: Resolver = default_dns_resolver) -> tuple[str, ...]:
        """Build hardened headless Chromium CLI invocation for read-only DOM dump."""
        canonical_url, _ = validate_public_url(url, resolver=resolver)
        return (self.binary, *HARDENED_CHROMIUM_ARGS, "--dump-dom", canonical_url)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 3,
        resolver: Resolver = default_dns_resolver,
    ) -> tuple[list[SearchHitOut], bool]:
        """Run search via Chromium runner when provided, or return explicitly marked stub results when offline."""
        clean_q = (query or "").strip()
        if not clean_q:
            raise WebResearchRefusal("invalid_query")

        if self._search_runner is not None:
            raw_items = await self._search_runner(clean_q, max_results)
            hits: list[SearchHitOut] = []
            for idx, item in enumerate(raw_items[:max_results], start=1):
                raw_url = str(item.get("url") or "").strip()
                canonical_url, _ = validate_public_url(raw_url, resolver=resolver)
                hits.append(
                    SearchHitOut(
                        rank=idx,
                        title=str(item.get("title") or canonical_url)[:240],
                        url=canonical_url,
                        snippet=str(item.get("snippet") or "")[:400],
                        provider="chromium_headless",
                        is_stub=False,
                        stub_badge="",
                    )
                )
            return hits, False

        return build_stub_search_hits(clean_q, max_results=max_results, provider="stub"), True

    async def fetch_rendered_page(
        self,
        url: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        resolver: Resolver = default_dns_resolver,
    ) -> RawHttpResponse:
        canonical_url, _ = validate_public_url(url, resolver=resolver)
        # CLI flags do not constrain Chromium's subresource, websocket, service
        # worker, or navigation egress. Until every browser request is forced
        # through an approved network boundary, do not execute a page runner.
        raise WebResearchRefusal("provider_unavailable", host=urlsplit(canonical_url).hostname or "")


def build_stub_search_hits(
    query: str,
    *,
    max_results: int = 3,
    provider: SearchProviderKind = "stub",
) -> list[SearchHitOut]:
    """Build deterministic, explicitly marked O-5 stub search hits that can never be mistaken for live evidence."""
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-") or "research-topic"
    slug = slug[:40]
    candidates = [
        (
            f"{STUB_SEARCH_MARKER} Обзор по запросу «{query}»",
            f"https://example.com/research/{slug}",
            f"{STUB_SEARCH_MARKER} Демонстрационный результат для «{query}». Не является живым источником.",
        ),
        (
            f"{STUB_SEARCH_MARKER} Справочные сведения: {query}",
            f"https://example.org/docs/{slug}",
            f"{STUB_SEARCH_MARKER} Справочная карточка (stub O-5) по теме «{query}».",
        ),
        (
            f"{STUB_SEARCH_MARKER} Публичный профиль и реквизиты: {query}",
            f"https://example.net/company/{slug}",
            f"{STUB_SEARCH_MARKER} Тестовая выдержка профиля для проверки пайплайна без платных вызовов.",
        ),
    ]
    return [
        SearchHitOut(
            rank=idx,
            title=title,
            url=url,
            snippet=snippet,
            provider=provider,
            is_stub=True,
            stub_badge=STUB_SEARCH_MARKER,
        )
        for idx, (title, url, snippet) in enumerate(candidates[:max_results], start=1)
    ]


async def _default_http_transport(
    url: str,
    timeout_seconds: float,
    max_bytes: int,
    *,
    target: OutboundTarget,
) -> RawHttpResponse:
    """GET one validated target IP while retaining the URL host for HTTP and TLS."""
    import httpx

    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout_seconds),
        trust_env=False,
        verify=True,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/xhtml+xml"},
    ) as client:
        # httpcore 1.x consumes sni_hostname for TLS negotiation and
        # certificate hostname verification while TCP connects to the IP URL.
        request = _build_pinned_request(client, url, target)
        resp = await client.send(request, stream=True)
        try:
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise WebResearchRefusal("response_too_large")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise WebResearchRefusal("response_too_large")
                chunks.append(chunk)
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return RawHttpResponse(
                status_code=resp.status_code,
                headers=headers,
                body=b"".join(chunks),
                final_url=url,
            )
        finally:
            await resp.aclose()


async def fetch_public_page(
    url: str,
    *,
    fetch_mode: FetchModeKind = "http_plain",
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    policy: StaticPolicy = StaticPolicy(),
    registry: RegistryReader = empty_registry,
    resolver: Resolver = default_dns_resolver,
    transport: HttpTransport | None = None,
    chromium_engine: ChromiumUnderHoodEngine | None = None,
    robots_override: str | None = None,
) -> PageSnapshotOut:
    """Fetch a public web page with SSRF/redirect/rebinding checks, robots.txt enforcement, and sanitization."""
    seen_resolutions: dict[str, set[str]] = {}
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    current_url, target = validate_public_url(
        url,
        policy=policy,
        registry=registry,
        resolver=resolver,
        seen_resolutions=seen_resolutions,
        is_redirect=False,
    )
    active_transport = transport or _default_http_transport
    redirect_chain: list[str] = []

    async def request(target_url: str, target: OutboundTarget, byte_limit: int, *, cap: float) -> RawHttpResponse:
        remaining = min(cap, deadline - asyncio.get_running_loop().time())
        if remaining <= 0:
            raise WebResearchRefusal("fetch_timeout", host=target.host)
        if transport is None:
            operation = _default_http_transport(target_url, remaining, byte_limit, target=target)
        else:
            operation = active_transport(target_url, remaining, byte_limit)
        try:
            return await asyncio.wait_for(operation, timeout=remaining)
        except (TimeoutError, asyncio.TimeoutError):
            raise WebResearchRefusal("fetch_timeout", host=target.host) from None

    for hop in range(max_redirects + 1):
        current_url, target = validate_public_url(
            current_url,
            policy=policy,
            registry=registry,
            resolver=resolver,
            seen_resolutions=seen_resolutions,
            is_redirect=(hop > 0),
        )
        parts = urlsplit(current_url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))

        robots_txt = robots_override
        robots_checked = robots_txt is not None
        if robots_txt is None:
            robots_url = f"{origin}/robots.txt"
            robots_url, robots_target = validate_public_url(
                robots_url,
                policy=policy,
                registry=registry,
                resolver=resolver,
                seen_resolutions=seen_resolutions,
                is_redirect=False,
            )
            try:
                robots_resp = await request(robots_url, robots_target, 64_000, cap=5.0)
                if robots_resp.status_code in _REDIRECT_STATUSES:
                    raise WebResearchRefusal("provider_unavailable", host=target.host)
                if 200 <= robots_resp.status_code < 300:
                    if len(robots_resp.body) > 64_000:
                        raise WebResearchRefusal("response_too_large", host=target.host)
                    ctype = (robots_resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                    if ctype not in {"text/plain", "application/octet-stream"}:
                        raise WebResearchRefusal("provider_unavailable", host=target.host)
                    robots_txt = robots_resp.body.decode("utf-8", errors="replace")
                elif robots_resp.status_code in {404, 410}:
                    robots_txt = ""
                else:
                    raise WebResearchRefusal("provider_unavailable", host=target.host)
                robots_checked = True
            except WebResearchRefusal:
                raise
            except Exception:
                raise WebResearchRefusal("provider_unavailable", host=target.host) from None

        if robots_txt and not is_allowed_by_robots(
            robots_txt, (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        ):
            raise WebResearchRefusal("robots_disallowed", host=target.host)

        try:
            if fetch_mode == "chromium_read_only":
                if chromium_engine is None:
                    raise WebResearchRefusal("provider_unavailable", host=target.host)
                resp = await chromium_engine.fetch_rendered_page(
                    current_url, timeout_seconds=timeout_seconds, max_bytes=max_bytes, resolver=resolver
                )
            else:
                resp = await request(current_url, target, max_bytes, cap=timeout_seconds)
        except WebResearchRefusal:
            raise
        except (TimeoutError, asyncio.TimeoutError):
            raise WebResearchRefusal("fetch_timeout", host=target.host) from None
        except Exception:
            raise WebResearchRefusal("provider_unavailable", host=target.host) from None

        if resp.status_code in _REDIRECT_STATUSES:
            if hop >= max_redirects:
                raise WebResearchRefusal("too_many_redirects", host=target.host)
            location = (resp.headers.get("location") or resp.headers.get("Location") or "").strip()
            if not location:
                raise WebResearchRefusal("redirect_blocked", host=target.host)
            next_url = urljoin(current_url, location)
            redirect_chain.append(current_url)
            current_url, target = validate_public_url(
                next_url,
                policy=policy,
                registry=registry,
                resolver=resolver,
                seen_resolutions=seen_resolutions,
                is_redirect=True,
            )
            continue

        if resp.status_code in {401, 403, 407}:
            raise WebResearchRefusal("login_or_captcha_blocked", host=target.host)
        if resp.status_code == 429:
            raise WebResearchRefusal("rate_limited", host=target.host)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise WebResearchRefusal("provider_unavailable", host=target.host)

        if len(resp.body) > max_bytes:
            raise WebResearchRefusal("response_too_large", host=target.host)

        raw_ctype = (resp.headers.get("content-type") or resp.headers.get("Content-Type") or "").strip()
        media_type = raw_ctype.split(";", 1)[0].strip().lower()
        if media_type not in ALLOWED_CONTENT_TYPES:
            raise WebResearchRefusal("unsupported_content_type", host=target.host)

        decoded = resp.body.decode("utf-8", errors="replace")
        extracted = sanitize_and_extract_page(decoded, content_type=media_type, fallback_title=target.host)
        fetched_at = datetime.now(timezone.utc)
        citation_anchor = f"{current_url}#t={fetched_at.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        limit_notes: list[str] = []
        if extracted["prompt_injection_detected"]:
            limit_notes.append("Heuristic prompt-injection markers were detected; external content remains untrusted.")
        if len(extracted["clean_text"]) > 8000:
            limit_notes.append("Текст страницы сокращён до 8000 символов.")

        return PageSnapshotOut(
            url=url,
            canonical_url=current_url,
            title=extracted["title"],
            excerpt=extracted["excerpt"],
            untrusted_body=extracted["untrusted_body"],
            content_hash=extracted["content_hash"],
            content_type=media_type,
            status_code=resp.status_code,
            byte_length=len(resp.body),
            fetched_at=fetched_at,
            fetch_mode=fetch_mode,
            redirect_chain=redirect_chain,
            robots_checked=robots_checked,
            prompt_injection_detected=extracted["prompt_injection_detected"],
            injection_markers=extracted["injection_markers"],
            extraction_limit_note=" ".join(limit_notes),
            is_stub=False,
            saved_snippet_id=None,
            citation_anchor=citation_anchor,
        )

    raise WebResearchRefusal("too_many_redirects")
