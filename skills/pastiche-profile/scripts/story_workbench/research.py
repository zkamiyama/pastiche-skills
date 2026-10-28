"""Bounded public-web acquisition with allowlists, robots and pinned connections.

Search is performed by the character-research Skill using the host agent's
search tool. This helper follows approved seed links; it is not a search engine
and does not bypass paywalls, authentication or JavaScript challenges.
"""
from __future__ import annotations
from collections import deque
from html.parser import HTMLParser
import http.client
import ipaddress
from pathlib import Path
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .store import Store, uid, dump, now, segments, chunked, atomic_json
from .protocol import Engine, Task, extract_prompt, parse_extract
from .pipeline import ingest

AGENT = "PasticheResearch/2.0"
MAX_BYTES = 2_000_000


def public_target(url: str, domains: set[str], *, allow_http=False) -> tuple[str, str, int, list[str]]:
    p = urlsplit(url)
    host = (p.hostname or "").lower().rstrip(".")
    if (p.scheme not in ({"https", "http"} if allow_http else {"https"}) or not host or
            host not in domains or p.username or p.password or any(c.isspace() for c in url) or "\\" in url):
        raise ValueError("URL scheme, credentials or domain is not allowed")
    port = p.port or (443 if p.scheme == "https" else 80)
    if port != (443 if p.scheme == "https" else 80):
        raise ValueError("nonstandard ports are not allowed")
    addresses = sorted({r[4][0] for r in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("private, loopback, link-local and reserved destinations are blocked")
    return p.scheme, host, port, addresses


def request_once(url: str, domains: set[str], *, allow_http=False) -> tuple[int, dict, bytes]:
    scheme, host, port, addresses = public_target(url, domains, allow_http=allow_http)
    # Connect to the already checked address: no second DNS lookup/rebinding.
    connection = http.client.HTTPConnection(host, port, timeout=20)
    sock = socket.create_connection((addresses[0], port), timeout=20)
    if scheme == "https":
        try:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        except BaseException:
            sock.close()
            raise
    connection.sock = sock
    p = urlsplit(url)
    try:
        connection.request("GET", urlunsplit(("", "", p.path or "/", p.query, "")),
                           headers={"User-Agent": AGENT, "Accept": "text/html,text/plain", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        if headers.get("content-encoding", "identity").lower() != "identity":
            raise ValueError("compressed response is unsupported")
        payload = response.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError("response exceeds size limit")
        return response.status, headers, payload
    finally:
        connection.close()


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts, self.links = [], []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        if not self.hidden:
            if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr", "section"}:
                self.parts.append("\n")
            if tag == "a":
                href = dict(attrs).get("href")
                if href:
                    self.links.append(href)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in {"p", "div", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def content(self):
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())


class Fetcher:
    def __init__(self, domains: set[str], *, delay=2.0, allow_http=False):
        if delay < 1:
            raise ValueError("crawl delay must be at least one second")
        self.domains = {d.lower().rstrip(".") for d in domains}
        self.delay, self.allow_http = delay, allow_http
        self.robots, self.last = {}, {}

    def request(self, url):
        host = urlsplit(url).hostname
        delay = self.delay
        robot = self.robots.get(urlsplit(url).scheme + "://" + urlsplit(url).netloc)
        if robot:
            delay = max(delay, robot.crawl_delay(AGENT) or 0, robot.crawl_delay("*") or 0)
        if delay > 60:
            raise ValueError("robots crawl delay exceeds this run's supported limit")
        time.sleep(max(0, delay - (time.monotonic() - self.last.get(host, -1e9))))
        try:
            return request_once(url, self.domains, allow_http=self.allow_http)
        finally:
            self.last[host] = time.monotonic()

    def permitted(self, url):
        public_target(url, self.domains, allow_http=self.allow_http)
        p = urlsplit(url)
        origin = p.scheme + "://" + p.netloc
        if origin not in self.robots:
            status, headers, raw = self.request(origin + "/robots.txt")
            robot = RobotFileParser()
            if status == 404:
                robot.parse(["User-agent: *", "Allow: /"])
            elif status == 200:
                robot.parse(raw.decode("utf-8", errors="replace").splitlines())
            else:
                # Conservative: do not treat fetch errors/redirects as permission.
                raise ValueError("robots policy unavailable; refusing acquisition")
            self.robots[origin] = robot
        if not self.robots[origin].can_fetch(AGENT, url):
            raise ValueError("robots policy disallows this URL")

    def fetch(self, url):
        for _ in range(4):
            self.permitted(url)
            status, headers, raw = self.request(url)
            if status in {301, 302, 303, 307, 308}:
                url = urljoin(url, headers.get("location", ""))
                continue
            if status != 200:
                raise ValueError(f"public page returned HTTP {status}")
            content_type = headers.get("content-type", "").lower()
            if not any(t in content_type for t in ("text/html", "text/plain", "application/xhtml+xml")):
                raise ValueError("only public HTML/plain text is supported")
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip(' "')
            try:
                decoded = raw.decode(charset)
            except (UnicodeError, LookupError) as exc:
                raise ValueError("unsupported or inconsistent page encoding") from exc
            parser = VisibleHTML()
            if "text/plain" in content_type:
                body, links = decoded, []
            else:
                parser.feed(decoded)
                body, links = parser.content(), parser.links
            if len(body.strip()) < 100:
                raise ValueError("page contains too little readable text; no browser/challenge bypass")
            return url, body, [urljoin(url, l) for l in links]
        raise ValueError("too many redirects")


def crawl(out: Path, seeds: list[str], domains: set[str], *, max_pages=8, depth=1,
          delay=2.0, allow_http=False, fetcher=None) -> list[dict]:
    if not seeds or not domains or not 1 <= max_pages <= 100 or not 0 <= depth <= 3:
        raise ValueError("explicit seeds/domains and bounded page/depth budgets are required")
    root = out / "research"
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources").mkdir(exist_ok=True)
    fetcher = fetcher or Fetcher(domains, delay=delay, allow_http=allow_http)
    queue, seen, saved = deque((u, 0, None) for u in seeds), set(), []
    attempts = 0
    while queue and attempts < max_pages:
        url, level, parent = queue.popleft()
        p = urlsplit(url)
        url = urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))
        if url in seen or (p.hostname or "").lower().rstrip(".") not in domains:
            continue
        seen.add(url)
        attempts += 1
        record = {"url": url, "parent": parent, "depth": level, "at": now()}
        try:
            final, body, links = fetcher.fetch(url)
            dest = root / "sources" / (uid("page", final, body) + ".txt")
            dest.write_text(body, encoding="utf-8")
            record.update(status="saved", final_url=final, path=str(dest.resolve()))
            saved.append(record)
            if level < depth:
                # Bound link fan-out as well as requests; no implicit domain expansion.
                for link in links[:200]:
                    if (urlsplit(link).hostname or "").lower().rstrip(".") in domains:
                        queue.append((link, level + 1, final))
        except (OSError, ValueError, http.client.HTTPException) as exc:
            record.update(status="failed", error=str(exc)[:200])
        with (root / "fetch-log.jsonl").open("a", encoding="utf-8") as f:
            f.write(dump(record) + "\n")
    (root / "map.md").write_text(
        ((root / "map.md").read_text(encoding="utf-8") if (root / "map.md").exists() else "# Character source map (private)\n")
        + "\n## Acquisition round " + now() + "\n\nURLs are leads, not verified character facts.\n\n"
        + "\n".join(f"- {r['final_url']} — {Path(r['path']).name}" for r in saved)
        + f"\n\nAttempts: {attempts}; saved: {len(saved)}. Unvisited links are not checked.\n", encoding="utf-8")
    return saved


def research(out: Path, seeds: list[str], domains: set[str], model, *, character: str,
             universe: str, scope: str, source_type="unclassified", max_pages=8, depth=1,
             delay=2.0, workers=1, retries=2, interval=15.0, allow_http=False, fetcher=None) -> dict:
    if source_type not in {"official", "creator", "licensed", "fan", "unclassified"}:
        raise ValueError("unknown source provenance category")
    out.mkdir(parents=True, exist_ok=True)
    audit_path = out / "research" / "audit.json"
    audit_base = {"target": character, "universe": universe, "scope": scope, "started_at": now()}
    atomic_json(audit_path, {**audit_base, "state": "running"})
    store = None
    try:
        saved = crawl(out, seeds, domains, max_pages=max_pages, depth=depth, delay=delay,
                      allow_http=allow_http, fetcher=fetcher)
        if not saved:
            raise ValueError("No readable sources acquired; inspect research/fetch-log.jsonl")
        store = Store(out / "character_graph.sqlite3")
        engine = Engine(store, model, out / "research", workers=workers, retries=retries, interval=interval)
        tasks, payload = [], {}
        for entry in saved:
            source, body = store.register_source(Path(entry["path"]), scope,
                                                 uri=entry["final_url"], source_type=source_type)
            for i, chunk in enumerate(chunked(segments(body), 3800)):
                key = f"web:{source['id']}:{i}"
                tasks.append(Task(key, extract_prompt(chunk, task="CHARACTER_WEB", target=character),
                                  lambda raw, c=chunk: parse_extract(raw, c)))
                payload[key] = (source, chunk, i, len(body))
        results = engine.run("character_web", tasks)
        count = 0
        for task in tasks:
            source, chunk, i, length = payload[task.id]
            # Cross-source exact names only within the explicit universe/canon.
            # Ambiguous aliases remain separate until a human merges them.
            _, _, claims = ingest(store, source, chunk, results[task.id], i, length,
                                  namespace="web:" + universe, temporal=False)
            count += len(claims)
        audit = {"state": "completed", "target": character, "universe": universe, "scope": scope,
                 "saved_pages": len(saved), "claims": count, **engine.stats,
                 "note": "Source categories were operator-supplied. All claims await human review; no search was performed by this helper."}
        atomic_json(out / "research" / "audit.json", audit)
        atomic_json(out / "character_graph.json", store.graph())
        return audit
    except BaseException as exc:
        atomic_json(audit_path, {**audit_base,
                    "state": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                    "error_type": type(exc).__name__, "finished_at": now()})
        raise
    finally:
        if store is not None:
            store.close()
