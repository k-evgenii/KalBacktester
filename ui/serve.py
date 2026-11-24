"""Local read-only web UI. Run from the repo root: uv run python -m ui.serve [--port 8765] [--db kalbt]"""
import argparse
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from db.connect import connect
from ui import runs
from ui.layout import esc, page

STATIC = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript",
    ".woff2": "font/woff2",
}


def static_file(name):
    """The file under ui/static for a URL name, or None if outside it or missing."""
    if "\x00" in name:
        return None
    path = (STATIC / name).resolve()
    if STATIC not in path.parents or not path.is_file():
        return None
    return path


def not_found_page():
    return page("Not found", '<div class="panel"><h1>Not found</h1></div>', "")


def error_page(error):
    message = f"{type(error).__name__}: {error}"
    body = f'<div class="panel panel-error"><h1>Server error</h1><p class="mono">{esc(message)}</p></div>'
    return page("Server error", body, "")


def make_server(port, dbname):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, content_type, data, extra_headers=()):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for key, value in extra_headers:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def send_html(self, status, text):
            self.send(status, "text/html; charset=utf-8", text.encode("utf-8"))

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                if path == "/":
                    self.send(302, "text/plain", b"", [("Location", "/runs")])
                elif path.startswith("/static/"):
                    self.send_static(unquote(path[len("/static/"):]))
                else:
                    self.send_db_page(path)
            except Exception as error:  # noqa: BLE001 - the 500 page shows it
                self.send_html(500, error_page(error))

        def send_static(self, name):
            path = static_file(name)
            content_type = CONTENT_TYPES.get(path.suffix) if path else None
            if content_type is None:
                self.send_html(404, not_found_page())
            else:
                self.send(200, content_type, path.read_bytes())

        def send_db_page(self, path):
            run_match = re.fullmatch(r"/runs/(\d{1,18})", path)
            if path != "/runs" and not run_match:
                self.send_html(404, not_found_page())
                return
            conn = connect(dbname)
            try:
                conn.read_only = True
                if run_match:
                    text = runs.run_page(conn, int(run_match.group(1)))
                else:
                    text = runs.runs_page(conn)
            finally:
                conn.close()
            if text is None:
                self.send_html(404, not_found_page())
            else:
                self.send_html(200, text)

        def method_not_allowed(self):
            self.send(405, "text/plain; charset=utf-8", b"Method not allowed", [("Allow", "GET")])

        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = method_not_allowed

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description="Serve the KalBacktester UI on 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default="kalbt")
    args = parser.parse_args()
    server = make_server(args.port, args.db)
    print(f"http://127.0.0.1:{server.server_address[1]}/runs")
    server.serve_forever()


if __name__ == "__main__":
    main()
