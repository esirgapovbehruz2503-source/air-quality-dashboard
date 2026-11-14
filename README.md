# Air Quality Monitoring and Short-Term PM2.5 Prediction Dashboard

**Module:** UDE5014CM – Data Science for Developers (Assessment 2)  
**Data Source Attribution:** Air quality model estimates are retrieved from the [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api) using the Copernicus Atmosphere Monitoring Service (**CAMS**) global domain (`cams_global`). Values represent model-based grid estimates, not direct ground-station measurements.

## Stakeholder & Checkpoint 1 Value Statement
* **Intended Stakeholder:** Urban residents and environmental information enthusiasts exploring how estimated pollution levels vary over time and across five European locations (London, Paris, Berlin, Madrid, Rome). This system does not provide official health or safety advice.
* **Checkpoint 1 Value Statement:** This component enables an urban resident or environmental information enthusiast to collect, validate, persist, and retrieve a genuine six-hourly CAMS air-quality estimate with full UTC provenance and a 30-minute acceptance check, resulting in a reliable, auditable primary record without manual downloads.

## Clean-Checkout Setup & Run Instructions (Checkpoint 1)

1. **Create and activate a virtual environment:**
   ```bash
   python -m venv .venv
   # Windows PowerShell:
   .venv\Scripts\Activate.ps1
   
## Checkpoint 2: Reliable Collection, SQL Queries & Data Quality
* **Checkpoint 2 Value Statement:** This component enables an urban resident or environmental information enthusiast to track multi-city CAMS air-quality collection across a 21-day schedule, inspect latest and daily-average PM2.5 estimates, and audit missing intervals without duplicate or revision corruption, resulting in a transparent, queryable 420-slot dataset.

### Run Assessed Collection & Stakeholder Queries
1. **Run scheduled assessed collection (marks expired slots missed and collects due 6-hour UTC slots):**
   ```bash
   python -m src.collector --mode assessed