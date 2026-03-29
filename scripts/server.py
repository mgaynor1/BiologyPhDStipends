import csv
import io
import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from xml.etree import ElementTree as ET
from zipfile import ZipFile


HOST = "127.0.0.1"
PORT = 8000
PHD_STIPENDS_URL = "https://www.phdstipends.com/csv"
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1rBOn60tBM6J2iCEbRQdH4Fb2sqCVKi90McpKwD8Tmlc/gviz/tq?tqx=out:csv&sheet=Sheet1"
EPI_FAMILY_BUDGET_URL = "https://files.epi.org/uploads/fbc_data_2026.xlsx"
ROOT = Path(__file__).resolve().parent
XML_NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def compact(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def parse_money(value):
    cleaned = compact(value).replace("$", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def slugify_county(value):
    value = compact(value).lower()
    value = re.sub(r"\b(city and borough|census area|municipality|borough|parish|county|city)\b", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return compact(value)


def clean_phd_university(value):
    value = compact(value)
    value = re.sub(r"\s\(.*$", "", value)
    value = re.sub(r"\s-\sSUNY$", "", value, flags=re.IGNORECASE)
    return compact(value)


def read_csv_from_url(url):
    with urlopen(url, timeout=20) as response:
        payload = response.read().decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(payload)))


def read_binary_from_url(url):
    with urlopen(url, timeout=30) as response:
        return response.read()


def read_xlsx_shared_strings(workbook):
    try:
        root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    shared = []
    for item in root.findall("a:si", XML_NS):
        shared.append("".join(node.text or "" for node in item.iterfind(".//a:t", XML_NS)))
    return shared


def read_xlsx_sheet_rows(payload, worksheet_name):
    workbook = ZipFile(io.BytesIO(payload))
    shared_strings = read_xlsx_shared_strings(workbook)
    workbook_xml = ET.fromstring(workbook.read("xl/workbook.xml"))
    rels_xml = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))

    rel_map = {}
    for rel in rels_xml:
        rel_map[rel.attrib.get("Id")] = rel.attrib.get("Target")

    target = None
    for sheet in workbook_xml.find("a:sheets", XML_NS):
        if sheet.attrib.get("name") == worksheet_name:
            target = rel_map.get(sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"))
            break

    if not target:
        raise ValueError(f"Worksheet {worksheet_name} not found in workbook.")

    sheet_root = ET.fromstring(workbook.read(f"xl/{target}"))
    row_nodes = sheet_root.find("a:sheetData", XML_NS).findall("a:row", XML_NS)
    rows = []

    for row in row_nodes:
        values = {}
        for cell in row.findall("a:c", XML_NS):
            ref = cell.attrib.get("r", "")
            match = re.match(r"([A-Z]+)", ref)
            if not match:
                continue
            column = match.group(1)
            value_node = cell.find("a:v", XML_NS)
            value = value_node.text if value_node is not None else ""
            if cell.attrib.get("t") == "s" and value:
                value = shared_strings[int(value)]
            values[column] = value
        rows.append(values)

    if len(rows) < 2:
        return []

    headers = rows[1]
    records = []
    for row in rows[2:]:
        record = {}
        for column, header in headers.items():
            record[compact(header)] = row.get(column, "")
        if any(compact(value) for value in record.values()):
            records.append(record)
    return records


def build_phd_comparison():
    phd_rows = read_csv_from_url(PHD_STIPENDS_URL)
    sheet_rows = read_csv_from_url(SHEET_CSV_URL)

    local_rows = {}
    for row in sheet_rows:
        university = compact(row.get("University_ID") or row.get("University"))
        department = compact(row.get("Department"))
        salary = parse_money(row.get("Annual guaranteed salary"))
        if not university or not department or salary is None:
            continue
        key = (university, department)
        local_rows.setdefault(key, []).append(salary)

    comparison = {}
    valid_years = {"2025-2026", "2024-2025", "2023-2024", "2022-2023", "2021-2022", "2020-2021", "2019-2020"}
    master_pattern = re.compile(r"(MS |M\.S\.|Masters|master|masters|Master| ms)")

    for row in phd_rows:
        university = clean_phd_university(row.get("University"))
        department = compact(row.get("Department"))
        comments = compact(row.get("Comments"))
        academic_year = compact(row.get("Academic Year") or row.get("Academic.Year"))
        overall_pay = parse_money(row.get("Overall Pay") or row.get("Overall.Pay"))
        fees = parse_money(row.get("Fees")) or 0
        adjusted_pay = None if overall_pay is None else overall_pay + fees

        if (
            not university
            or not department
            or adjusted_pay is None
            or adjusted_pay <= 100
            or academic_year not in valid_years
            or master_pattern.search(comments)
        ):
            continue

        key = (university, department)
        if key not in local_rows:
            continue

        comparison.setdefault(key, [])
        for salary in local_rows[key]:
            comparison[key].append(adjusted_pay - salary)

    rows = []
    for (university, department), values in comparison.items():
        mean_difference = sum(values) / len(values)
        rows.append({
            "label": f"{university} - {department}",
            "mean": mean_difference,
            "type": "positive" if mean_difference >= 0 else "negative"
        })

    rows.sort(key=lambda row: row["mean"], reverse=True)
    return rows


def build_epi_county_budget():
    rows = read_xlsx_sheet_rows(read_binary_from_url(EPI_FAMILY_BUDGET_URL), "County")
    lookup = {}
    for row in rows:
        family = compact(row.get("Family"))
        state = compact(row.get("State abv."))
        county = compact(row.get("County"))
        annual_total = parse_money(row.get("Total"))
        county_fips = compact(row.get("county_fips"))

        if family != "1p0c" or not state or not county or annual_total is None:
            continue

        key = f"{state}|{slugify_county(county)}"
        lookup[key] = {
            "state": state,
            "county": county,
            "county_fips": county_fips,
            "family": family,
            "annual_total": annual_total
        }

    return {
        "source": "EPI Family Budget Map",
        "year": 2026,
        "family": "1p0c",
        "records": lookup
    }


class BiologyHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/phdstipends-live.csv", "/phdstipends-live.csv?"):
            self.serve_phd_stipends()
            return
        if self.path in ("/phdstipends-comparison.json", "/phdstipends-comparison.json?"):
            self.serve_phd_comparison()
            return
        if self.path in ("/epi-family-budget.json", "/epi-family-budget.json?"):
            self.serve_epi_family_budget()
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

    def serve_phd_comparison(self):
        try:
            payload = json.dumps(build_phd_comparison()).encode("utf-8")
        except HTTPError as error:
            self.send_error(error.code, f"Unable to fetch comparison inputs: {error.reason}")
            return
        except URLError as error:
            self.send_error(502, f"Unable to reach comparison inputs: {error.reason}")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def serve_epi_family_budget(self):
        try:
            payload = json.dumps(build_epi_county_budget()).encode("utf-8")
        except HTTPError as error:
            self.send_error(error.code, f"Unable to fetch EPI family budget data: {error.reason}")
            return
        except URLError as error:
            self.send_error(502, f"Unable to reach EPI family budget data: {error.reason}")
            return
        except ValueError as error:
            self.send_error(500, str(error))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
