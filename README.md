# Universal Exhibitor Scraping Agent

An autonomous, deterministic-first, security-hardened intelligence agent for extracting trade show exhibitor directories, catalogues, and participant lists into Microsoft Excel (`.xlsx`), CSV, and JSON.

![Universal Exhibitor Agent Architecture](Universal%20Exhibitor%20Intelligence%20Architecture.png)

---

## Key Features

- **Double-Click Setup & Execution:** Complete Windows one-click automation via `setup.bat` and `run.bat`.
- **Modern Web Dashboard:** Beautiful, responsive single-page web UI built with FastAPI & Tailwind CSS.
- **Instant Excel Export:** Zero-dependency OpenXML `.xlsx` generator with styled headers, alternating rows, and auto-adjusted column widths.
- **Deep Profile Enrichment:** Automatically crawls and enriches individual exhibitor profile pages for corporate websites, emails, phone numbers, booth locations, and company descriptions.
- **Multi-Platform Scraping Engines:**
  - **Swapcard GraphQL Engine:** Cursor-based Relay pagination fetching complete catalogues (e.g. CPHI, Informa, EventPlanner) plus single profile extractor.
  - **A2Z Events Engine:** Offset-based pagination with automated chunking (e.g. SEMICON India).
  - **MMIConnect Engine:** Direct GraphQL batch customer resolution.
  - **Universal HTML & Table Engine:** Generic card, list, and table parsers with multi-column card support.
  - **Controlled Playwright Fallback:** Strict, resource-bounded headless browser escalation only when JS rendering evidence is detected.
- **Production-Grade SSRF Protection:** Physical socket-level IP pinning, DNS rebinding mitigation, safe DNS adapters, origin re-validation across redirects, and streaming response size bounds.
- **Comprehensive Test Suite:** 293 deterministic tests passing with 100% reliability.

---

## Quick Start (Windows — Double-Click)

### 1. One-Time Setup
Double-click **`setup.bat`**:
- Checks for Python (3.10+).
- Creates the local virtual environment (`.venv`).
- Installs dependencies from `requirements.txt`.
- Installs Playwright Chromium browser binaries.

### 2. Run Application
Double-click **`run.bat`**:
- Activates the virtual environment.
- Starts the FastAPI web server on `http://127.0.0.1:8000`.
- Automatically opens the web dashboard in your default browser!

---

## Manual Installation (Cross-Platform)

```bash
# Clone the repository
git clone https://github.com/basheer1236/Exhibitor_agent.git
cd Exhibitor_agent

# Create and activate virtual environment
python -m venv .venv

# On Windows:
.venv\Scripts\activate
# On Linux / macOS:
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright browser
python -m playwright install chromium

# Launch the Web Interface
python run_ui.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your web browser.

---

## Scraping Priority Architecture

Outbound requests strictly adhere to a deterministic-first hierarchy:

```
                      TARGET URL
                          │
                          ▼
                     Discovery
                          │
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
       JSON API         HTML         IFRAME
            │             │             │
            └─────────────┼─────────────┘
                          │
                    Useful records?
                      │         │
                     YES        NO
                      │         │
                      ▼         ▼
                    Done     Browser Fallback (Strict Escalation)
                               │
                               ▼
                         Rendered DOM
                               │
                               ▼
                       Deterministic Parser
```

1. **API Discovery:** Discovers internal JSON, REST, and GraphQL endpoints.
2. **Deterministic HTML Extraction:** Server-rendered HTML and table parsers.
3. **Iframe Extraction:** Bounded traversal (depth = 1) of embedded directory iframes.
4. **Browser Fallback (`BROWSER_HTML`):** Last-resort rendering via headless Chromium, invoked only when deterministic methods find 0 records and JavaScript evidence is confirmed.

---

## API Endpoints

The agent exposes a RESTful API:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web Dashboard user interface |
| `POST` | `/api/scrape` | Executes scraping job for target URL |
| `GET` | `/api/download/excel?job_id=...` | Downloads formatted Microsoft Excel (`.xlsx`) |
| `GET` | `/api/download/csv?job_id=...` | Downloads CSV export (`.csv`) |
| `GET` | `/api/download/json?job_id=...` | Downloads raw structured JSON (`.json`) |
| `GET` | `/api/pipeline/stream` | Server-Sent Events (SSE) status stream |
| `GET` | `/docs` | Interactive Swagger API documentation |

---

## Running Automated Tests

Run the full deterministic test suite with `pytest`:

```bash
python -m pytest tests -v
```

All **293 tests** run offline with mocked network boundaries, validating:
- SSRF prevention, IP pinning, and DNS rebinding protections.
- Universal HTML extraction, table parsing, and pagination.
- Swapcard GraphQL Relay pagination and Apollo cache extraction.
- Profile enrichment, phone normalization (Indian 10-digit standard & international), and email filtering.
- Web UI endpoints and OpenXML Excel file generation.

---

## License

MIT License. Developed for enterprise trade show directory intelligence.
