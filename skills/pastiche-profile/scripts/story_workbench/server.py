"""Loopback-only, token-protected evidence review UI. No runtime CDN or Node.js."""
from __future__ import annotations
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from json_boundary import loads as strict_loads, read_object
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit

from .store import Store, Conflict, dump
from .writing import prepare

ASSETS = Path(__file__).with_name("web")


def make_server(out: Path, port=8765, token: str | None = None):
    out = out.resolve()
    if not (out / "character_graph.sqlite3").is_file():
        raise ValueError("character_graph.sqlite3 is missing; run analysis/research/demo first")
    token = token or secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log tokens, source URLs, or private text.

        def send(self, status, payload, content_type="application/json; charset=utf-8"):
            data = payload if isinstance(payload, bytes) else dump(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self, api=False):
            bound_port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{bound_port}", f"localhost:{bound_port}"}
            if self.headers.get("Host") not in hosts:
                self.send(403, {"error": "Host not allowed"})
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin not in {"http://"+h for h in hosts}:
                self.send(403, {"error": "Origin not allowed"})
                return False
            if api and not hmac.compare_digest(self.headers.get("X-Pastiche-Token", ""), token):
                self.send(403, {"error": "Session token required"})
                return False
            return True

        def do_GET(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            if not self.authorized(api=path.startswith("/api/")):
                return
            static = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in static:
                name, mime = static[path]
                return self.send(200, (ASSETS / name).read_bytes(), mime)
            store = Store(out / "character_graph.sqlite3")
            try:
                query = parse_qs(parsed.query)
                key = query.get("id", [""])[0]
                if path == "/api/graph":
                    data = store.graph()
                    from .world import conflict_candidates
                    data["world_conflicts"] = conflict_candidates([c for c in data["claims"]
                        if c.get("domain") == "world" and c["status"] in {"auto_accepted", "human_approved"} and not c["stale"]])
                    data["sources"] = [{k: s[k] for k in ("id", "label", "scope", "source_type")} for s in store.rows("sources")]
                elif path == "/api/perspectives":
                    scope = query.get("scope", [""])[0]
                    if not scope or scope == "*":
                        raise ValueError("select one scope")
                    data = store.perspective_context(scope, float(query.get("cutoff", [1])[0]))
                elif path == "/api/perspective-plan":
                    from .epistemic import simulate
                    plan_path = out / "writing" / "story_blueprint.json"
                    if plan_path.is_file():
                        plan = read_object(plan_path)
                        data = simulate(plan["epistemic"], plan["scenes"], plan["characters"])
                    else:
                        data = {"events": [], "note": "No authored perspective timeline yet."}
                elif path == "/api/world":
                    data = store.world_graph()
                elif path == "/api/evidence":
                    data = store.resolve_evidence(key)
                elif path == "/api/history":
                    data = store.history(key)
                elif path == "/api/stories":
                    data = {}
                    for name in ("story_maps.json", "plot_patterns.json", "story_audit.json", "world_audit.json"):
                        p = out / name
                        data[name] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
                else:
                    return self.send(404, {"error": "Not found"})
                self.send(200, data)
            except Conflict as exc:
                self.send(409, {"error": str(exc)})
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.send(400, {"error": str(exc)[:400]})
            finally:
                store.close()

        def do_POST(self):
            if not self.authorized(api=True):
                return
            store = None
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536 or self.headers.get_content_type() != "application/json":
                    return self.send(400, {"error": "A bounded JSON request is required"})
                body = strict_loads(self.rfile.read(length), maximum=65536)
                if not isinstance(body, dict):
                    raise ValueError("JSON body must be an object")
                store = Store(out / "character_graph.sqlite3")
                path = urlsplit(self.path).path
                if path == "/api/review":
                    result = store.review(body["id"], body["action"], body["revision"], body["reviewer"],
                                          body.get("note", ""), body.get("patch"))
                elif path == "/api/merge":
                    store.merge(body["source"], body["target"], body["reviewer"], body["note"])
                    result = {"ok": True}
                elif path == "/api/unmerge":
                    store.unmerge(body["source"], body["reviewer"], body["note"])
                    result = {"ok": True}
                elif path == "/api/entity":
                    result = {"id": store.entity("manual", body["name"], body["scope"], entity_type=body.get("entity_type", "character"))}
                elif path == "/api/claim":
                    body["model_checked"] = False
                    result = {"id": store.add_claim(body)}
                elif path == "/api/layout":
                    store.save_layout(body)
                    result = {"ok": True}
                elif path == "/api/layout/reset":
                    store.db.execute("DELETE FROM layout")
                    store.db.commit()
                    result = {"ok": True}
                elif path == "/api/export":
                    if not body.get("scope") or body["scope"] == "*":
                        raise ValueError("Select one canon scope before exporting")
                    result = prepare(out, body["scope"], body.get("cutoff", 1), mode=body.get("mode", "original"),
                                     world_mode=body.get("world_mode", "original"))
                else:
                    return self.send(404, {"error": "Not found"})
                self.send(200, result)
            except Conflict as exc:
                self.send(409, {"error": str(exc)})
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.send(400, {"error": str(exc)[:400]})
            finally:
                if store:
                    store.close()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server, token


def serve(out: Path, port=8765) -> None:
    server, token = make_server(out, port)
    print(f"Local evidence workbench: http://127.0.0.1:{server.server_address[1]}/#token={token}", flush=True)
    print("Keep this URL private. Ctrl-C stops the server. No public bind or automatic upload.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
