"""
Modern FastAPI Web Interface for Universal Exhibitor Agent.
Provides URL input, live execution, interactive data inspection,
and one-click downloads in Excel (.xlsx), CSV, and JSON formats.
"""

import asyncio
import datetime
import json
import re
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, HttpUrl

from app.agents.universal_scraper import scrape
from app.models.scrape_budget import ScrapeBudget
from app.ui.excel_exporter import records_to_csv, records_to_xlsx


app = FastAPI(
    title="Universal Exhibitor Agent",
    description="Production-grade AI Exhibitor Directory Scraper with SSRF protection",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage for recent scrape jobs
JOB_STORE: dict[str, dict[str, Any]] = {}


class ScrapeRequest(BaseModel):
    url: str
    max_records: int = 1000
    max_pages: int = 50
    timeout_seconds: int = 30
    max_requests: int = 1000
    enrich_details: bool = True
    max_detail_requests: Optional[int] = 1000


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the modern, single-page web UI dashboard."""
    return HTMLResponse(content=HTML_DASHBOARD)


@app.post("/api/scrape")
async def api_scrape(payload: ScrapeRequest):
    """Executes the universal exhibitor scraper for the given directory URL."""
    target_url = payload.url.strip()
    if not target_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    budget = ScrapeBudget(
        max_records=payload.max_records,
        max_pages=payload.max_pages,
        timeout_seconds=payload.timeout_seconds,
        max_requests=payload.max_requests,
        enrich_details=payload.enrich_details,
        max_detail_requests=payload.max_detail_requests,
    )

    loop = asyncio.get_running_loop()
    try:
        # Run blocking scraper in default thread pool to avoid blocking the async event loop
        result = await loop.run_in_executor(None, lambda: scrape(target_url, budget=budget))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Scraper error: {exc}")

    job_id = uuid.uuid4().hex
    records_data = [r.model_dump() for r in result.records]
    
    # Extract domain for cleaner filenames
    parsed = urlparse(target_url)
    domain_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", parsed.netloc or "directory")

    job_entry = {
        "job_id": job_id,
        "url": target_url,
        "domain_slug": domain_slug,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "success": result.success,
        "strategy": result.strategy,
        "records_count": len(records_data),
        "pages_scraped": result.pages_scraped,
        "requests_made": result.requests_made,
        "duration_seconds": round(result.duration_seconds, 2),
        "quality_status": getattr(result, "quality_status", "UNKNOWN"),
        "quality_score": round(getattr(result, "quality_score", 0.0), 2),
        "errors": result.errors,
        "records": records_data,
    }
    
    JOB_STORE[job_id] = job_entry
    
    # Prune old jobs (keep most recent 50)
    if len(JOB_STORE) > 50:
        oldest = sorted(JOB_STORE.keys(), key=lambda k: JOB_STORE[k]["created_at"])[:-50]
        for k in oldest:
            JOB_STORE.pop(k, None)

    return job_entry


@app.get("/api/download/excel")
async def download_excel(job_id: str):
    """Exports records from the specified job as a styled Microsoft Excel (.xlsx) file."""
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired.")

    records = job["records"]
    xlsx_bytes = records_to_xlsx(records)
    filename = f"exhibitors_{job['domain_slug']}_{job_id[:8]}.xlsx"

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/download/csv")
async def download_csv(job_id: str):
    """Exports records from the specified job as a CSV file."""
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired.")

    records = job["records"]
    csv_str = records_to_csv(records)
    filename = f"exhibitors_{job['domain_slug']}_{job_id[:8]}.csv"

    return Response(
        content=csv_str.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/download/json")
async def download_json(job_id: str):
    """Exports raw JSON results from the specified job."""
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired.")

    json_str = json.dumps(job, indent=2, ensure_ascii=False)
    filename = f"exhibitors_{job['domain_slug']}_{job_id[:8]}.json"

    return Response(
        content=json_str.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/pipeline/stream")
async def pipeline_stream():
    """Server-Sent Events endpoint for pipeline status streaming."""
    async def event_generator():
        payload = json.dumps({"status": "ready", "message": "Universal Exhibitor Agent pipeline active"})
        yield f"data: {payload}\n\n"
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
        },
    )


HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Universal Exhibitor Agent | Intelligent Directory Scraper</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: {
            sans: ['"Plus Jakarta Sans"', 'sans-serif'],
            mono: ['"JetBrains Mono"', 'monospace'],
          },
          colors: {
            brand: {
              50: '#f0fdf4',
              100: '#dcfce7',
              500: '#22c55e',
              600: '#16a34a',
              700: '#15803d',
            },
            accent: {
              500: '#06b6d4',
              600: '#0891b2',
            }
          }
        }
      }
    }
  </script>
  <style>
    body {
      background: radial-gradient(circle at 50% 0%, #1e293b 0%, #0f172a 75%, #020617 100%);
      min-height: 100vh;
    }
    .glass {
      background: rgba(30, 41, 59, 0.7);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }
    .glass-card {
      background: rgba(15, 23, 42, 0.75);
      backdrop-filter: blur(12px);
      border: 1px solid rgba(255, 255, 255, 0.06);
    }
    .pulse-glow {
      box-shadow: 0 0 25px -5px rgba(34, 197, 94, 0.35);
    }
    /* Custom scrollbar */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: #0f172a; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #475569; }
  </style>
</head>
<body class="text-slate-100 font-sans antialiased selection:bg-brand-500 selection:text-white">

  <!-- Navigation Bar -->
  <header class="border-b border-slate-800/80 glass sticky top-0 z-50">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-brand-600 to-accent-500 flex items-center justify-center shadow-lg shadow-brand-500/20">
          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/>
          </svg>
        </div>
        <div>
          <span class="text-lg font-bold tracking-tight text-white flex items-center gap-2">
            Universal Exhibitor Agent
            <span class="text-[11px] font-mono uppercase px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              SSRF Hardened
            </span>
          </span>
          <p class="text-xs text-slate-400">Autonomous Trade Show & Expo Directory Intelligence</p>
        </div>
      </div>
      
      <div class="flex items-center space-x-4">
        <div class="flex items-center space-x-2 text-xs font-mono text-emerald-400 bg-emerald-950/40 px-3 py-1.5 rounded-lg border border-emerald-800/40">
          <span class="relative flex h-2 w-2">
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
          </span>
          <span>Engine Active</span>
        </div>
      </div>
    </div>
  </header>

  <!-- Main Container -->
  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">

    <!-- Hero / URL Input Box -->
    <div class="glass rounded-2xl p-6 sm:p-8 shadow-2xl relative overflow-hidden border border-slate-700/50">
      <div class="absolute -top-24 -right-24 w-96 h-96 bg-brand-500/10 rounded-full blur-3xl pointer-events-none"></div>
      <div class="absolute -bottom-24 -left-24 w-96 h-96 bg-accent-500/10 rounded-full blur-3xl pointer-events-none"></div>

      <div class="relative z-10 max-w-3xl">
        <h1 class="text-2xl sm:text-3xl font-extrabold text-white tracking-tight">
          Extract Exhibitors to Excel
        </h1>
        <p class="mt-2 text-sm text-slate-300">
          Paste any trade show exhibitor directory, catalogue, or participants list URL.
          The engine automatically detects API endpoints, handles pagination, enriches contact details, and formats the output into Excel (.xlsx).
        </p>
      </div>

      <form id="scrapeForm" class="mt-6 space-y-4">
        <div class="relative flex flex-col sm:flex-row gap-3">
          <div class="relative flex-grow">
            <div class="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none text-slate-400">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/>
              </svg>
            </div>
            <input 
              type="url" 
              id="targetUrl" 
              required
              placeholder="https://example-expo.org/exhibitor-directory" 
              class="w-full pl-12 pr-4 py-3.5 bg-slate-900/90 border border-slate-700 rounded-xl text-white placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500/60 focus:border-brand-500 transition shadow-inner font-mono"
            />
          </div>

          <button 
            type="submit" 
            id="runBtn"
            class="sm:w-auto px-7 py-3.5 bg-gradient-to-r from-emerald-500 to-brand-600 hover:from-emerald-600 hover:to-brand-700 text-white font-semibold text-sm rounded-xl shadow-lg shadow-emerald-900/40 hover:shadow-emerald-900/60 transition duration-200 flex items-center justify-center gap-2 flex-shrink-0 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <span id="btnIcon">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/>
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 1 0 0118 0z"/>
              </svg>
            </span>
            <span id="btnText">Run Scraper</span>
          </button>
        </div>

        <!-- Advanced Options Toggle -->
        <div class="pt-2">
          <button 
            type="button" 
            id="optionsToggle" 
            class="text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1.5 transition font-medium focus:outline-none"
          >
            <svg id="optionsArrow" class="w-4 h-4 transition-transform duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
            </svg>
            <span>Extraction Settings & Budgets</span>
          </button>

          <div id="optionsPanel" class="hidden mt-4 grid grid-cols-1 sm:grid-cols-3 md:grid-cols-6 gap-4 p-4 rounded-xl bg-slate-900/60 border border-slate-800">
            <div>
              <label class="block text-xs font-semibold text-slate-400 mb-1">Max Records</label>
              <input type="number" id="optMaxRecords" value="1000" min="1" max="10000" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white focus:ring-1 focus:ring-brand-500 focus:outline-none font-mono"/>
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-400 mb-1">Max Pages</label>
              <input type="number" id="optMaxPages" value="50" min="1" max="200" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white focus:ring-1 focus:ring-brand-500 focus:outline-none font-mono"/>
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-400 mb-1">Timeout (s)</label>
              <input type="number" id="optTimeout" value="30" min="5" max="120" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white focus:ring-1 focus:ring-brand-500 focus:outline-none font-mono"/>
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-400 mb-1">Max Requests</label>
              <input type="number" id="optMaxRequests" value="1000" min="1" max="5000" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white focus:ring-1 focus:ring-brand-500 focus:outline-none font-mono"/>
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-400 mb-1">Max Profile Details</label>
              <input type="number" id="optMaxDetailReqs" value="1000" min="0" max="5000" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white focus:ring-1 focus:ring-brand-500 focus:outline-none font-mono"/>
            </div>
            <div class="flex items-center pt-5">
              <label class="inline-flex items-center gap-2 cursor-pointer text-xs font-semibold text-slate-300">
                <input type="checkbox" id="optEnrichDetails" checked class="w-4 h-4 rounded text-brand-500 bg-slate-950 border-slate-700 focus:ring-brand-500 focus:ring-offset-slate-900"/>
                <span>Enrich Profiles</span>
              </label>
            </div>
          </div>
        </div>
      </form>
    </div>

    <!-- Live Execution Status Bar -->
    <div id="statusBar" class="hidden glass rounded-xl p-4 border border-slate-800 flex items-center justify-between animate-fade-in">
      <div class="flex items-center space-x-3">
        <svg class="animate-spin h-5 w-5 text-emerald-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <div>
          <p id="statusMsg" class="text-sm font-semibold text-white">Analyzing directory and discovering endpoints...</p>
          <p class="text-xs text-slate-400">Enforcing SSRF destination policy and DNS rebinding protections</p>
        </div>
      </div>
    </div>

    <!-- Error Banner -->
    <div id="errorBanner" class="hidden rounded-xl p-4 bg-rose-950/50 border border-rose-800/80 text-rose-200 text-sm flex items-start gap-3">
      <svg class="w-5 h-5 text-rose-400 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 1 0 0118 0z"/>
      </svg>
      <div>
        <p class="font-bold text-rose-300">Extraction Error</p>
        <p id="errorText" class="mt-0.5 text-rose-200 text-xs"></p>
      </div>
    </div>

    <!-- Results Section -->
    <div id="resultsSection" class="hidden space-y-6">

      <!-- Metrics KPI Grid -->
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div class="glass-card rounded-xl p-4 border border-slate-800">
          <p class="text-xs text-slate-400 font-medium">Records Extracted</p>
          <p id="metricRecords" class="text-2xl font-extrabold text-emerald-400 mt-1 font-mono">0</p>
        </div>
        <div class="glass-card rounded-xl p-4 border border-slate-800">
          <p class="text-xs text-slate-400 font-medium">Strategy</p>
          <p id="metricStrategy" class="text-lg font-bold text-white mt-1.5 font-mono">-</p>
        </div>
        <div class="glass-card rounded-xl p-4 border border-slate-800">
          <p class="text-xs text-slate-400 font-medium">Duration</p>
          <p id="metricDuration" class="text-2xl font-bold text-white mt-1 font-mono">0s</p>
        </div>
      </div>

      <!-- Download Action Bar -->
      <div class="glass rounded-xl p-4 border border-slate-700/80 flex flex-col sm:flex-row items-center justify-between gap-4">
        <div class="flex items-center space-x-2 text-sm text-slate-300">
          <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 1 0 0118 0z"/>
          </svg>
          <span class="font-medium text-white">Extraction Ready for Export</span>
          <span class="text-xs text-slate-400">(Available in Excel .xlsx, CSV, and raw JSON)</span>
        </div>

        <div class="flex items-center space-x-2.5 w-full sm:w-auto">
          <!-- EXCEL DOWNLOAD BUTTON -->
          <a 
            id="downloadExcelBtn" 
            href="#" 
            class="flex-1 sm:flex-none inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs tracking-wide uppercase transition shadow-lg shadow-emerald-950/60"
          >
            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/>
              <path d="m9.5 13.5 1.5 2.5-1.5 2.5h1.5l.8-1.5.8 1.5h1.4l-1.5-2.5 1.5-2.5H13l-.8 1.5-.7-1.5H9.5z"/>
            </svg>
            Download Excel (.xlsx)
          </a>

          <!-- CSV DOWNLOAD BUTTON -->
          <a 
            id="downloadCsvBtn" 
            href="#" 
            class="flex-1 sm:flex-none inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 font-semibold text-xs tracking-wide transition"
          >
            Download CSV
          </a>

          <!-- JSON DOWNLOAD BUTTON -->
          <a 
            id="downloadJsonBtn" 
            href="#" 
            class="flex-1 sm:flex-none inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 font-semibold text-xs tracking-wide transition"
          >
            JSON
          </a>
        </div>
      </div>

      <!-- Data Table with Live Search -->
      <div class="glass-card rounded-xl border border-slate-800 overflow-hidden shadow-xl">
        <div class="p-4 border-b border-slate-800/80 flex flex-col sm:flex-row justify-between items-center gap-3">
          <div class="flex items-center space-x-2">
            <h2 class="text-sm font-bold text-white uppercase tracking-wider">Extracted Exhibitors</h2>
            <span id="filteredCount" class="text-xs text-slate-400 font-mono"></span>
          </div>

          <div class="relative w-full sm:w-72">
            <input 
              type="text" 
              id="searchBox" 
              placeholder="Search companies, booth, city..." 
              class="w-full bg-slate-900 border border-slate-700 rounded-lg pl-8 pr-3 py-1.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
            <svg class="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
            </svg>
          </div>
        </div>

        <div class="overflow-x-auto max-h-[550px]">
          <table class="w-full text-left text-xs border-collapse">
            <thead class="bg-slate-900/90 text-slate-400 sticky top-0 uppercase tracking-wider font-mono text-[11px] border-b border-slate-800">
              <tr>
                <th class="py-3 px-4">#</th>
                <th class="py-3 px-4">Company Name</th>
                <th class="py-3 px-4">Website</th>
                <th class="py-3 px-4">Contact</th>
                <th class="py-3 px-4">Booth / Stand</th>
                <th class="py-3 px-4">Hall</th>
                <th class="py-3 px-4">Location</th>
              </tr>
            </thead>
            <tbody id="recordsTableBody" class="divide-y divide-slate-800/60 font-sans">
              <!-- Dynamically populated rows -->
            </tbody>
          </table>
        </div>
      </div>

    </div>

  </main>

  <script>
    let currentJob = null;
    let allRecords = [];

    // Toggle advanced options
    const optionsToggle = document.getElementById('optionsToggle');
    const optionsPanel = document.getElementById('optionsPanel');
    const optionsArrow = document.getElementById('optionsArrow');
    optionsToggle.addEventListener('click', () => {
      optionsPanel.classList.toggle('hidden');
      optionsArrow.classList.toggle('rotate-180');
    });

    // Handle form submit
    const scrapeForm = document.getElementById('scrapeForm');
    const runBtn = document.getElementById('runBtn');
    const btnText = document.getElementById('btnText');
    const btnIcon = document.getElementById('btnIcon');
    const statusBar = document.getElementById('statusBar');
    const errorBanner = document.getElementById('errorBanner');
    const errorText = document.getElementById('errorText');
    const resultsSection = document.getElementById('resultsSection');
    const searchBox = document.getElementById('searchBox');

    scrapeForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const url = document.getElementById('targetUrl').value.trim();
      if (!url) return;

      // Lock UI
      runBtn.disabled = true;
      btnText.innerText = "Extracting...";
      statusBar.classList.remove('hidden');
      errorBanner.classList.add('hidden');
      resultsSection.classList.add('hidden');

      const payload = {
        url: url,
        max_records: parseInt(document.getElementById('optMaxRecords').value) || 1000,
        max_pages: parseInt(document.getElementById('optMaxPages').value) || 50,
        timeout_seconds: parseInt(document.getElementById('optTimeout').value) || 30,
        max_requests: parseInt(document.getElementById('optMaxRequests')?.value || 1000) || 1000,
        enrich_details: document.getElementById('optEnrichDetails') ? document.getElementById('optEnrichDetails').checked : true,
        max_detail_requests: parseInt(document.getElementById('optMaxDetailReqs')?.value || 1000) || 1000,
      };

      try {
        const response = await fetch('/api/scrape', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || 'Scraping failed');
        }

        currentJob = data;
        allRecords = data.records || [];
        displayResults(data);

      } catch (err) {
        errorBanner.classList.remove('hidden');
        errorText.innerText = err.message || 'An unexpected error occurred during extraction.';
      } finally {
        runBtn.disabled = false;
        btnText.innerText = "Run Scraper";
        statusBar.classList.add('hidden');
      }
    });

    function displayResults(data) {
      resultsSection.classList.remove('hidden');

      // Populate metrics
      document.getElementById('metricRecords').innerText = data.records_count;
      document.getElementById('metricStrategy').innerText = data.strategy;
      document.getElementById('metricDuration').innerText = data.duration_seconds + 's';

      // Setup download links
      document.getElementById('downloadExcelBtn').href = `/api/download/excel?job_id=${data.job_id}`;
      document.getElementById('downloadCsvBtn').href = `/api/download/csv?job_id=${data.job_id}`;
      document.getElementById('downloadJsonBtn').href = `/api/download/json?job_id=${data.job_id}`;

      renderTable(allRecords);
    }

    function renderTable(records) {
      const tbody = document.getElementById('recordsTableBody');
      const filteredCount = document.getElementById('filteredCount');
      tbody.innerHTML = '';
      filteredCount.innerText = `(${records.length} shown)`;

      if (records.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="7" class="py-8 text-center text-slate-500 font-mono text-xs">
              No matching exhibitor records found.
            </td>
          </tr>
        `;
        return;
      }

      records.forEach((r, idx) => {
        const tr = document.createElement('tr');
        tr.className = 'hover:bg-slate-800/40 transition-colors text-slate-300';

        const webHtml = r.website 
          ? `<a href="${r.website}" target="_blank" rel="noopener noreferrer" class="text-brand-400 hover:text-brand-300 hover:underline flex items-center gap-1 font-mono">${truncate(r.website, 28)}</a>`
          : `<span class="text-slate-600">-</span>`;

        const contactHtml = (r.email || r.phone)
          ? `<div class="space-y-0.5">
               ${r.email ? `<div class="text-sky-400 font-mono text-[11px]">${r.email}</div>` : ''}
               ${r.phone ? `<div class="text-slate-400 font-mono text-[11px]">${r.phone}</div>` : ''}
             </div>`
          : `<span class="text-slate-600">-</span>`;

        const locHtml = [r.city, r.country].filter(Boolean).join(', ') || '<span class="text-slate-600">-</span>';

        tr.innerHTML = `
          <td class="py-3 px-4 font-mono text-slate-500">${idx + 1}</td>
          <td class="py-3 px-4 font-semibold text-white">${escapeHtml(r.company_name || 'Unnamed')}</td>
          <td class="py-3 px-4">${webHtml}</td>
          <td class="py-3 px-4">${contactHtml}</td>
          <td class="py-3 px-4 font-mono text-emerald-300">${escapeHtml(r.booth || '-')}</td>
          <td class="py-3 px-4 font-mono text-slate-400">${escapeHtml(r.hall || '-')}</td>
          <td class="py-3 px-4">${locHtml}</td>
        `;
        tbody.appendChild(tr);
      });
    }

    // Filter table by search
    searchBox.addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase().trim();
      if (!q) {
        renderTable(allRecords);
        return;
      }
      const filtered = allRecords.filter(r => {
        const text = [
          r.company_name,
          r.website,
          r.email,
          r.phone,
          r.booth,
          r.hall,
          r.city,
          r.country,
          r.description
        ].filter(Boolean).join(' ').toLowerCase();
        return text.includes(q);
      });
      renderTable(filtered);
    });

    function truncate(str, maxLen) {
      if (!str) return '';
      return str.length > maxLen ? str.slice(0, maxLen) + '...' : str;
    }

    function escapeHtml(text) {
      if (!text) return '';
      return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }
  </script>
</body>
</html>
"""
