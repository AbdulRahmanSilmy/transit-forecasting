"""Serve one trusted Plotly HTML export for a temporary browser check."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    """Serve a startup snapshot of one HTML file on loopback HTTP.

    Notes
    -----
    Accepts an HTML file as a positional command-line argument. An available
    port is chosen automatically and the preview URL is printed immediately.
    All paths other than ``/plot.html`` return HTTP 404. Stop the process after
    validation; no directories or other files are exposed.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html_file", type=Path, help="Trusted HTML export to preview.")
    args = parser.parse_args()
    path = args.html_file.resolve()
    if not path.is_file() or path.suffix.lower() not in (".html", ".htm"):
        parser.error("html_file must be an existing .html or .htm file.")
    html = path.read_bytes()

    class PlotlyHandler(BaseHTTPRequestHandler):
        """Respond only with the selected immutable HTML snapshot."""

        def do_GET(self) -> None:
            """Return the HTML snapshot or an explicit HTTP 404.

            Notes
            -----
            The preview is not cached, and no request path is used to resolve
            filesystem content.
            """
            if self.path != "/plot.html":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html)

    server = ThreadingHTTPServer(("127.0.0.1", 0), PlotlyHandler)
    print(f"http://127.0.0.1:{server.server_port}/plot.html", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Plotly preview stopped.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
