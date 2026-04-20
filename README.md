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

3. Or generate directly from pasted text (no temp files needed):

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

- `accent_color` (preferred)
- `primary_color_hex` (legacy fallback, still supported)

## Why this is intentionally basic

This is designed as a fast internal MVP:

- no database
- no auth
- no web UI
- minimal assumptions

If needed, this can later be wrapped in a tiny web interface (Flask/Streamlit) with copy/paste text areas.
