import sys
import json
from pathlib import Path
from app.agents.universal_scraper import scrape
from app.models.scrape_budget import ScrapeBudget


def main():
    if len(sys.argv) > 1:
        url = sys.argv[1].strip()
    else:
        url = input("Enter Exhibitor Directory URL (e.g. https://www.ihexpo.com/exhibitor-directory/): ").strip()

    if not url:
        print("Error: No URL provided.")
        return

    print(f"\n=======================================================")
    print(f" Scraping: {url}")
    print(f"=======================================================\n")

    result = scrape(url, budget=ScrapeBudget(enrich_details=True))

    print(f"Status:       {'SUCCESS' if result.success else 'FAILED'}")
    print(f"Strategy:     {result.strategy}")
    print(f"Records:      {len(result.records)}")
    print(f"Quality:      {result.quality_status} (Score: {result.quality_score:.2f})")
    print(f"Duration:     {result.duration_seconds:.2f}s")
    if result.errors:
        print(f"Errors:       {result.errors}")

    # Ask or automatically save output
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    
    # Generate clean filename from URL host
    from urllib.parse import urlparse
    host = urlparse(url).netloc.replace(".", "_") or "results"
    out_file = out_dir / f"{host}_records.json"

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

    print(f"\n[OK] Full results saved to: {out_file.resolve()}")
    if result.records:
        print(f"\nSample Exhibitors:")
        for idx, rec in enumerate(result.records[:3], 1):
            print(f"\n  [{idx}] {rec.company_name}")
            print(f"      Website:    {rec.website or 'N/A'}")
            print(f"      Detail URL: {rec.detail_url or 'N/A'}")
            print(f"      Email:      {rec.email or 'N/A'}" + (f" (all {len(rec.emails)}: {', '.join(rec.emails)})" if len(rec.emails) > 1 else ""))
            print(f"      Phone:      {rec.phone or 'N/A'}" + (f" (all {len(rec.phones)}: {', '.join(rec.phones)})" if len(rec.phones) > 1 else ""))
            print(f"      Address:    {rec.address or 'N/A'}")
            print(f"      Booth/Hall: Stall {rec.booth or 'N/A'} (Hall {rec.hall or 'N/A'})")
            print(f"      Categories: {', '.join(rec.category[:3]) if rec.category else 'N/A'}")
            if rec.description:
                print(f"      Desc:       {rec.description[:90]}...")


if __name__ == "__main__":
    main()
