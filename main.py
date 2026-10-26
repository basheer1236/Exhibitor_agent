from typing import Optional
from fastapi import FastAPI
from pydantic import BaseModel, HttpUrl

from app.agents.universal_scraper import scrape
from app.models.scrape_budget import ScrapeBudget


app = FastAPI(
    title="Universal Exhibitor Agent",
    description="Deterministic exhibitor scraping service",
    version="1.0.0",
)


class ScrapeRequest(BaseModel):
    url: HttpUrl
    max_pages: Optional[int] = None
    max_records: Optional[int] = None
    max_requests: Optional[int] = None
    enrich_details: bool = True
    max_detail_requests: Optional[int] = None


@app.get("/")
def root():
    return {
        "message": "Universal Exhibitor Agent is running"
    }


@app.post("/scrape")
def scrape_website(request: ScrapeRequest):
    budget_kwargs = {
        "enrich_details": request.enrich_details,
    }
    if request.max_pages is not None:
        budget_kwargs["max_pages"] = request.max_pages
    if request.max_records is not None:
        budget_kwargs["max_records"] = request.max_records
    if request.max_requests is not None:
        budget_kwargs["max_requests"] = request.max_requests
    if request.max_detail_requests is not None:
        budget_kwargs["max_detail_requests"] = request.max_detail_requests

    budget = ScrapeBudget(**budget_kwargs)
    result = scrape(str(request.url), budget=budget)
    return result.model_dump()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)