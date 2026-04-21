# artus-docs-tool

Very basic internal tool to turn copy-pasted table data into two PDFs:

- Order Confirmation
- Proforma Invoice

## Quick start

1. Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

2. Generate documents using sample data files:

```bash
python3 generate_pdfs.py \
  --meta sample_data/meta.tsv \
  --items sample_data/items.tsv \
  --branding branding.example.json \
  --output-dir output
```

This produces:

- `output/order_confirmation_<document_number>.pdf`
- `output/proforma_<document_number>.pdf`

3. Generate from your raw copied order format (recommended for your workflow):

```bash
python3 generate_pdfs.py \
  --order-file sample_data/order_raw.txt \
  --branding branding.example.json \
  --output-dir output
```

You can also pass the raw block directly with `--order-text`.

4. Or generate directly from pasted table text (legacy split mode, no temp files needed):

```bash
python3 generate_pdfs.py \
  --meta-text $'key\tvalue\ndocument_number\tOC-10024\ndate\t2026-04-20\ncustomer_name\tPaste Test\ncurrency\tEUR' \
  --items-text $'description\tquantity\tunit_price\ttax_rate\nWidget A\t2\t10\t0.2' \
  --branding branding.example.json \
  --output-dir output
```

## Input format

### 1) Metadata table (`--meta`)

Simple 2-column table (TSV/CSV/semicolon/pipe all supported):

| key             | value      |
|-----------------|------------|
| document_number | OC-10023   |
| date            | 2026-04-20 |
| customer_name   | ACME Ltd   |
| currency        | EUR        |

Recommended keys:

- `document_number`
- `date`
- `customer_name`
- `customer_email`
- `customer_address`
- `currency`

### 2) Items table (`--items`)

Header row + line items:

| description | quantity | unit_price | tax_rate |
|-------------|----------|------------|----------|
| Service A   | 2        | 120.00     | 0.20     |

Required columns:

- `description`
- `quantity`
- `unit_price`

Optional column:

- `tax_rate` (defaults to `0`)

## Branding / style config (`--branding`)

Pass a JSON file with basic header/footer/logo settings. See `branding.example.json`.

If `logo_path` is omitted or file is missing, PDFs are generated without logo.

Color keys:

- `primary_color_hex` (main brand color)
- `accent_color` (secondary/highlight color)

Current example uses your requested palette:

- `primary_color_hex`: `#004225`
- `accent_color`: `#CCBF9E`

## Current document template rules

### Order Confirmation

- Header: Artus logo, then `Order Confirmation`, then `UMA Order Reference: <ref>`
- Footer:
  - Spectrum Supply t/a Umbra Shading, 31 Ystrad Road, Fforestfach, Swansea, SA5 4BT.
  - 01792 562015 sales@umbrashading.co.uk.
  - Company Registration No. 7317206.
  - The above pricing excludes VAT.

### Proforma Invoice

- Header: Artus logo, then `Proforma Invoice`, then `UMA Order Reference: <ref>`
- Also shows:
  - `Invoice Date` = date of generation
  - `Due Date` = date of generation
- Footer includes the same legal/company text as Order Confirmation, plus:
  - Account Name: Spectrum Supply Ltd
  - Sort Code: 12-20-26
  - Account Number: 01874691

## Why this is intentionally basic

This is designed as a fast internal MVP:

- no database
- no auth
- no web UI
- minimal assumptions

If needed, this can later be wrapped in a tiny web interface (Flask/Streamlit) with copy/paste text areas.

## Deploy-ready web UI (Streamlit)

This repo now includes a very basic web UI (`app.py`) for internal use:

- paste raw UMA order text
- click "Generate PDFs"
- download Order Confirmation + Proforma directly

Recent UX updates:

- input paste box now starts blank (no default sample text)
- logo rendering keeps original aspect ratio to avoid stretching
- proforma now shows a prominent "Bank Details" panel above footer content

### Run locally

```bash
streamlit run app.py
```

### Optional internal password protection

Set environment variable `APP_PASSWORD`. If set, users must enter this password before generating PDFs.

Example local run:

```bash
APP_PASSWORD="your-internal-password" streamlit run app.py
```

## Render deployment

This repo includes:

- `render.yaml` (Blueprint deployment config)
- `Procfile` (fallback process command)

The app starts with:

```bash
streamlit run app.py --server.port $PORT --server.address 0.0.0.0
```

After deployment, upload your logo to the repository at:

- `assets/artus-logo.png`

and ensure branding config points to that path (`branding.example.json` already does).
