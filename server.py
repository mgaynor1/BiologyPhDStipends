from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


HOST = "127.0.0.1"
PORT = 8000
PHD_STIPENDS_URL = "https://www.phdstipends.com/csv"
ROOT = Path(__file__).resolve().parent


class BiologyHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
      if self.path in ("/phdstipends-live.csv", "/phdstipends-live.csv?"):
        self.serve_phd_stipends()
        return
      super().do_GET()

    def serve_phd_stipends(self):
      try:
        with urlopen(PHD_STIPENDS_URL, timeout=20) as response:
          payload = response.read()
      except HTTPError as error:
        self.send_error(error.code, f"Unable to fetch PhD Stipends CSV: {error.reason}")
        return
      except URLError as error:
        self.send_error(502, f"Unable to reach PhD Stipends CSV: {error.reason}")
        return

      self.send_response(200)
      self.send_header("Content-Type", "text/csv; charset=utf-8")
      self.send_header("Cache-Control", "no-store")
      self.send_header("Content-Length", str(len(payload)))
      self.end_headers()
      self.wfile.write(payload)


def main():
    server = ThreadingHTTPServer((HOST, PORT), BiologyHandler)
    print(f"Serving {ROOT} at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
