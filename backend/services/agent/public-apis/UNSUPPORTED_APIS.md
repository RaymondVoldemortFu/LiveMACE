# Unsupported or Wrapped APIs

This file lists APIs that cannot be fully implemented locally or via a free data source
with complete accuracy. These APIs are wrapped with APIVerve HTTP requests instead.

## airquality

- Reason: Requires authoritative, up-to-date air quality measurements and official AQI
  calculations (including US EPA and GB DEFRA index). Free sources do not provide all
  required indexes consistently for global cities.
- Needed resources: An official air quality provider that exposes pollutant levels and
  both US EPA and GB DEFRA indices (or the full pollutant set needed to compute them).
- Possible sources:
  - APIVerve Air Quality API (`https://api.apiverve.com/v1/airquality`)
  - National/official air quality providers with complete index data

## bimivalidator

- Reason: Full validation requires DNS resolution, BIMI record parsing, SVG/VMC fetching,
  and certificate checks with strict compliance rules.
- Needed resources: BIMI validation rules and certificate verification tooling.
- Possible sources:
  - APIVerve BIMI Validator API (`https://api.apiverve.com/v1/bimivalidator`)

## bucketlist

- Reason: Requires a large, curated bucket list dataset with broad coverage.
- Needed resources: A comprehensive bucket list content source.
- Possible sources:
  - APIVerve Bucket List API (`https://api.apiverve.com/v1/bucketlist`)

## caaparser

- Reason: Accurate interpretation needs CA metadata, tag meanings, and policy guidance
  beyond raw record parsing.
- Needed resources: CAA policy metadata and authoritative CA information.
- Possible sources:
  - APIVerve CAA Record Parser API (`https://api.apiverve.com/v1/caaparser`)

## carmodels

- Reason: Requires a full vehicle make/model/trim/specs dataset across years.
- Needed resources: Comprehensive vehicle data (e.g., EPA or OEM datasets).
- Possible sources:
  - APIVerve Car Models API (`https://api.apiverve.com/v1/carmodels`)

## charades

- Reason: Needs large, curated word lists by category to avoid limited local lists.
- Needed resources: Category-based word datasets for charades.
- Possible sources:
  - APIVerve Charades API (`https://api.apiverve.com/v1/charades`)

## countydata

- Reason: County metrics require authoritative, merged datasets across many fields.
- Needed resources: County-level demographic, health, and cost datasets.
- Possible sources:
  - APIVerve County Data API (`https://api.apiverve.com/v1/countydata`)

## htmltoimage

- Reason: Reliable HTML-to-image rendering requires a headless browser (e.g. Puppeteer/Playwright)
  or a dedicated screenshot service. Pure-Python or minimal-dependency solutions do not produce
  faithful layout/CSS rendering.
- Needed resources: Headless browser stack or a screenshot/rendering API.
- Possible sources:
  - APIVerve HTML to Image API (`https://api.apiverve.com/v1/htmltoimage`)
  - Self-hosted Puppeteer/Playwright service

## htmltopdf

- Reason: Faithful HTML-to-PDF (with CSS, fonts, layout) requires a headless browser or
  a dedicated PDF engine (e.g. wkhtmltopdf, WeasyPrint, or Chromium). Not feasible with
  minimal dependencies in this project.
- Needed resources: Headless browser or PDF rendering service.
- Possible sources:
  - APIVerve HTML to PDF API (`https://api.apiverve.com/v1/htmltopdf`)
  - Self-hosted headless Chrome or WeasyPrint

## webscreenshots

- Reason: Website screenshots require a headless browser or screenshot service to render
  the page and capture pixels. Not feasible with minimal dependencies.
- Possible sources:
  - APIVerve Web Screenshots API
  - Self-hosted Puppeteer/Playwright

## websitetopdf

- Reason: Same as htmltopdf; full-page PDF from URL requires headless browser or
  dedicated PDF-from-URL service.
- Possible sources:
  - APIVerve Website to PDF API
  - Self-hosted headless Chrome

