"""
Supreme Energy SCM Portal – Service Receipt PDF Link Extractor
==============================================================
Correctly implements the 3-step login flow discovered from the portal HTML:

  Step 1  GET  /log_in_in            → get session cookie
  Step 2  POST /log_in_in/cekuser    → load user's department list
  Step 3  POST /log_in_in/cek_login  → authenticate; returns "sukses"
  Step 4  GET  /home                 → finalise session (server sets
                                        remaining session variables here)

Then:
  POST /query/sr/ajax_list  (DataTables server-side)  → SR list for Agreement NO
  GET  /service_receipt/sr/view/{SR_NO}               → find "View/Download" PDF URL

Output: formatted table on screen + CSV file.

Requirements:
    pip install requests beautifulsoup4

Usage:
    python sr_scraper.py
"""

import re
import sys
import csv
import time
import getpass
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing libraries. Run:  pip install requests beautifulsoup4")


# ── Base URLs ──────────────────────────────────────────────────────────────────
BASE          = "https://scm.supreme-energy.com"
URL_LOGIN_GET = f"{BASE}/log_in_in"
URL_CEKUSER   = f"{BASE}/log_in_in/cekuser"
URL_CEK_LOGIN = f"{BASE}/log_in_in/cek_login"
URL_HOME      = f"{BASE}/home"
URL_QUERY_SR  = f"{BASE}/query/sr"
URL_AJAX_LIST = f"{BASE}/query/sr/ajax_list"
URL_SR_VIEW   = f"{BASE}/service_receipt/sr/view"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
PAGE_SIZE  = 100    # rows per AJAX page
NUM_COLS   = 9      # columns in the DataTables table

# Output CSV column names
OUT_COLS = ["AGREEMENT NO", "ITP NO", "SERVICE RECEIPT NO", "LINK TO PDF"]


# ══════════════════════════════════════════════════════════════════════════════
# 1.  LOGIN
# ══════════════════════════════════════════════════════════════════════════════

def get_department(session: requests.Session, username: str) -> str:
    """
    Call /log_in_in/cekuser to get the department list for this username.
    Returns the selected department ID (prompts user if more than one).
    """
    r = session.post(
        URL_CEKUSER,
        data={"username": username},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": URL_LOGIN_GET,
        },
        timeout=20,
    )
    r.raise_for_status()

    options = BeautifulSoup(r.text, "html.parser").find_all("option")
    if not options:
        # Nothing came back – use the page default
        return "101016100"

    if len(options) == 1:
        dept_id = options[0]["value"]
        print(f"  Department      : {options[0].get_text(strip=True)} ({dept_id})")
        return dept_id

    # Multiple departments – ask the user
    print("\n  Available departments:")
    for i, opt in enumerate(options, 1):
        print(f"    [{i}] {opt.get_text(strip=True)} (ID: {opt['value']})")
    while True:
        choice = input(f"  Select department [1-{len(options)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            sel = options[int(choice) - 1]
            return sel["value"]
        print("  Invalid choice, try again.")


def login(session: requests.Session, username: str, password: str) -> None:
    """
    Full three-step CodeIgniter login sequence.
    Raises RuntimeError if authentication fails.
    """
    # ── 1. Seed the session (get any initial cookies) ─────────────────────────
    print("  → Opening login page …", end=" ", flush=True)
    session.get(URL_LOGIN_GET, timeout=20)
    print("done")

    # ── 2. Fetch department list ───────────────────────────────────────────────
    print("  → Fetching department list …", end=" ", flush=True)
    dept_id = get_department(session, username)
    print() if dept_id else None   # newline already printed inside

    # ── 3. Authenticate ───────────────────────────────────────────────────────
    print("  → Submitting credentials …", end=" ", flush=True)
    login_payload = {
        "username":           username,
        "password":           password,
        "ID_DEPARTMENT":      dept_id,
        "choose_application": "scm",
        "lang":               "ENG",   # only option in the select
        "chaptcha":           "",      # hidden; only shown after repeated failures
    }
    r = session.post(
        URL_CEK_LOGIN,
        data=login_payload,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer":          URL_LOGIN_GET,
            "Accept":           "*/*",
        },
        timeout=20,
    )
    r.raise_for_status()

    response_text = r.text.strip().strip('"').strip("'")
    if response_text.lower() != "sukses":
        raise RuntimeError(
            f"Login rejected by server.\n"
            f"  Server said: {r.text.strip()[:200]!r}\n"
            f"  Check username / password / department."
        )
    print("success ✓")

    # ── 4. Finalise session – visit /home ─────────────────────────────────────
    print("  → Establishing session (GET /home) …", end=" ", flush=True)
    session.get(URL_HOME, timeout=20, allow_redirects=True)
    print("done")


# ══════════════════════════════════════════════════════════════════════════════
# 2.  FETCH SR LIST VIA DataTables AJAX
# ══════════════════════════════════════════════════════════════════════════════

def _build_dt_payload(draw: int, start: int, agreement_no: str) -> dict:
    """
    Minimal DataTables server-side POST body.
    Matches what the browser's jQuery DataTables plugin sends when the
    user clicks 'Filter' on /query/sr.
    """
    p: dict = {
        # ── DataTables paging / draw ───────────────────────────────────────
        "draw":              str(draw),
        "start":             str(start),
        "length":            str(PAGE_SIZE),
        "search[value]":     "",
        "search[regex]":     "false",
        # ── Custom server-side filter fields (read by the CI controller) ───
        "po_no":             agreement_no,   # "AGREEMENT NO" in the UI
        "itp_no":            "",
        "itp_description":   "",
        "department":        "0",            # "0" = All
        "sr_no":             "",
    }
    # DataTables 1.10 columns array (some CI handlers require it)
    for i in range(NUM_COLS):
        p[f"columns[{i}][data]"]          = str(i)
        p[f"columns[{i}][name]"]          = ""
        p[f"columns[{i}][searchable]"]    = "true"
        p[f"columns[{i}][orderable]"]     = "false"
        p[f"columns[{i}][search][value]"] = ""
        p[f"columns[{i}][search][regex]"] = "false"
    return p


def fetch_all_rows(session: requests.Session, agreement_no: str) -> list:
    """
    Paginate through the DataTables AJAX endpoint and return every row.
    Each row is a list of HTML-cell strings as returned by the server.
    """
    print(f"  Endpoint  : {URL_AJAX_LIST}")
    print(f"  Filter    : AGREEMENT NO = {agreement_no!r}\n")

    ajax_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer":          URL_QUERY_SR,
        "Accept":           "application/json, text/javascript, */*; q=0.01",
        "Origin":           BASE,
    }

    all_rows: list = []
    draw  = 1
    start = 0

    while True:
        payload = _build_dt_payload(draw, start, agreement_no)

        try:
            r = session.post(
                URL_AJAX_LIST,
                data=payload,
                headers=ajax_headers,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Network error on AJAX call: {exc}")

        # ── Detect the JS-redirect "session guard" response ────────────────
        body = r.text.strip()
        if body.startswith("<script>") and "document.location" in body:
            target = (re.search(r"href='([^']+)'", body) or
                      re.search(r'href="([^"]+)"', body))
            dest = target.group(1) if target else "?"
            raise RuntimeError(
                f"Server returned a session-redirect to: {dest}\n"
                f"  The session was not accepted by the AJAX endpoint.\n"
                f"  This should not happen after a successful login.\n"
                f"  Try running the script again; if it persists, the portal\n"
                f"  may have timed out the session between login and the AJAX call."
            )

        if not r.ok:
            raise RuntimeError(
                f"HTTP {r.status_code} from AJAX endpoint.\n"
                f"  URL  : {r.url}\n"
                f"  Body : {body[:400]}"
            )

        try:
            result = r.json()
        except ValueError:
            raise RuntimeError(
                f"AJAX response is not JSON.\n"
                f"  Content-Type : {r.headers.get('Content-Type', '?')}\n"
                f"  Body snippet : {body[:400]}"
            )

        rows = result.get("data", [])
        if not rows:
            print("  (No rows on this page – done.)")
            break

        all_rows.extend(rows)
        total_filtered = int(result.get("recordsFiltered", 0) or len(all_rows))
        total_all      = result.get("recordsTotal", "?")

        print(
            f"  Page {draw:>2}: rows {start+1}–{start+len(rows)}"
            f"  |  matched={total_filtered}, total in DB={total_all}"
        )

        start += PAGE_SIZE
        draw  += 1
        if start >= total_filtered:
            break
        time.sleep(0.4)

    return all_rows


# ══════════════════════════════════════════════════════════════════════════════
# 3.  PARSE DataTables CELLS
# ══════════════════════════════════════════════════════════════════════════════
# Column layout (0-based):
#   0  AGREEMENT NO     <a href=".../show_po/…">24100228-OS-10101</a>
#   1  ITP NO           <a href=".../detail/…">25000142-TP-10101</a>
#   2  ITP DESCRIPTION  plain text
#   3  SERVICE RECEIPT  <a href=".../view/…">25000464-SR-10101</a>
#                       <a …><i class="fa fa-print"></i></a>   ← skip
#   4  OV NO
#   5  DEPARTMENT
#   6  SYNC STATUS
#   7  VALUE
#   8  (action)

def _link_text(cell_html) -> str:
    """Return the visible text of the first <a> tag (or plain text if none)."""
    s = BeautifulSoup(str(cell_html), "html.parser")
    a = s.find("a")
    return (a or s).get_text(strip=True)


def _parse_sr_cell(cell_html) -> tuple[str, str]:
    """
    Return (sr_no, absolute_view_url) from the SR NO cell.
    The cell contains:
      • Anchor 1: view link  (plain text = SR number, no <i> inside)
      • Anchor 2: print button (contains <i class='fa fa-print'>)
    """
    s = BeautifulSoup(str(cell_html), "html.parser")
    for a in s.find_all("a"):
        if not a.find("i"):                          # skip icon-only buttons
            text = a.get_text(strip=True)
            href = urljoin(BASE, a.get("href", ""))
            if text:
                return text, href
    # Fallback: construct URL from text
    plain = s.get_text(strip=True).split()[0] if s.get_text(strip=True) else ""
    return plain, f"{URL_SR_VIEW}/{plain}"


# ══════════════════════════════════════════════════════════════════════════════
# 4.  GET PDF URL FROM SR DETAIL PAGE
# ══════════════════════════════════════════════════════════════════════════════

def get_pdf_url(session: requests.Session, sr_view_url: str) -> str:
    """
    Visit /service_receipt/sr/view/{SR_NO} and find the PDF URL.

    The portal renders a "View/Download" link whose href points to the PDF:
      https://scm.supreme-energy.com/upload/service_receipt/{AGREEMENT_NO}/{hash}.pdf
    """
    try:
        r = session.get(
            sr_view_url,
            headers={"Referer": URL_QUERY_SR},
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        return f"ERROR: {exc}"

    s = BeautifulSoup(r.text, "html.parser")

    # Strategy 1 – anchor whose visible text contains "View/Download"
    for a in s.find_all("a", href=True):
        if re.search(r"view\s*/\s*download", a.get_text(strip=True), re.I):
            return urljoin(BASE, a["href"])

    # Strategy 2 – href ends with .pdf
    for a in s.find_all("a", href=True):
        if a["href"].lower().endswith(".pdf"):
            return urljoin(BASE, a["href"])

    # Strategy 3 – href path contains /upload/service_receipt/
    for a in s.find_all("a", href=True):
        if "/upload/service_receipt/" in a["href"]:
            return urljoin(BASE, a["href"])

    # Strategy 4 – href mentions download / file / attachment
    for a in s.find_all("a", href=True):
        h = a["href"].lower()
        if any(k in h for k in ("download", "/pdf", "/file/", "attachment")):
            return urljoin(BASE, a["href"])

    return "N/A"


# ══════════════════════════════════════════════════════════════════════════════
# 5.  OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

def print_table(rows: list[dict]) -> None:
    if not rows:
        print("  (No results to display)")
        return
    widths = [
        max(len(h), max(len(str(r[h])) for r in rows))
        for h in OUT_COLS
    ]
    bar = "=" * (sum(widths) + 3 * (len(widths) - 1))
    fmt = "   ".join(f"{{:<{w}}}" for w in widths)
    sep = "   ".join("-" * w for w in widths)
    print("\n" + bar)
    print(fmt.format(*OUT_COLS))
    print(sep)
    for r in rows:
        print(fmt.format(*(str(r[c]) for c in OUT_COLS)))
    print(bar)


def save_csv(rows: list[dict], agreement_no: str) -> str:
    safe  = re.sub(r"[^\w\-]", "_", agreement_no)
    fname = f"SR_List_{safe}.csv"
    with open(fname, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return fname


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 62)
    print("  Supreme Energy SCM Portal  –  SR PDF Link Extractor")
    print("=" * 62)

    # ── Collect inputs ─────────────────────────────────────────────────────────
    print("\n[1/4] Credentials & Agreement Number")
    username     = input("  Username     : ").strip()
    password     = getpass.getpass("  Password     : ")
    agreement_no = input("\n  AGREEMENT NO : ").strip()

    if not all([username, password, agreement_no]):
        sys.exit("ERROR: username, password, and AGREEMENT NO are all required.")

    # ── Login ──────────────────────────────────────────────────────────────────
    print("\n[2/4] Logging in …")
    session = requests.Session()
    session.headers.update({
        "User-Agent": BROWSER_UA,
        "Accept-Language": "en-US,en;q=0.9",
    })

    try:
        login(session, username, password)
    except RuntimeError as exc:
        sys.exit(f"\n  ✗ {exc}")

    # ── Fetch SR list ──────────────────────────────────────────────────────────
    print("\n[3/4] Fetching Service Receipt list …\n")
    try:
        raw_rows = fetch_all_rows(session, agreement_no)
    except RuntimeError as exc:
        sys.exit(f"\n  ✗ {exc}")

    if not raw_rows:
        sys.exit(
            f"\n  No Service Receipt records found for AGREEMENT NO = {agreement_no!r}.\n"
            f"  Verify the agreement number and try again."
        )

    print(f"\n  ✓ {len(raw_rows)} record(s) retrieved.")

    # ── Extract PDF links ──────────────────────────────────────────────────────
    print("\n[4/4] Extracting PDF links from each Service Receipt page …\n")
    results: list[dict] = []

    for idx, row in enumerate(raw_rows, 1):
        agr_no        = _link_text(row[0])          # col 0: AGREEMENT NO
        itp_no        = _link_text(row[1])          # col 1: ITP NO
        sr_no, sr_url = _parse_sr_cell(row[3])      # col 3: SERVICE RECEIPT NO

        print(f"  [{idx:>3}/{len(raw_rows)}] {sr_no:<30}", end=" ", flush=True)

        pdf_url = get_pdf_url(session, sr_url)
        ok      = pdf_url not in ("N/A",) and not pdf_url.startswith("ERROR")
        print("✓" if ok else "–", " ", pdf_url)

        results.append({
            "AGREEMENT NO":       agr_no,
            "ITP NO":             itp_no,
            "SERVICE RECEIPT NO": sr_no,
            "LINK TO PDF":        pdf_url,
        })
        time.sleep(0.2)

    # ── Print & save ───────────────────────────────────────────────────────────
    print_table(results)
    fname = save_csv(results, agreement_no)
    print(f"\n  CSV saved → {fname}")
    print()


if __name__ == "__main__":
    main()