#!/usr/bin/env python3
"""
Very basic internal PDF generator.

Given:
- metadata table (key/value)
- items table (line items)
- branding config JSON

It creates:
- order confirmation PDF
- proforma invoice PDF
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    Image,
)


SUPPORTED_DELIMITERS = [",", "\t", ";", "|"]


@dataclass
class Item:
    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal

    @property
    def net_total(self) -> Decimal:
        return self.quantity * self.unit_price

    @property
    def tax_amount(self) -> Decimal:
        return self.net_total * self.tax_rate

    @property
    def gross_total(self) -> Decimal:
        return self.net_total + self.tax_amount


def detect_delimiter(sample_text: str) -> str:
    best_delim = ","
    best_count = -1
    for delim in SUPPORTED_DELIMITERS:
        count = sample_text.count(delim)
        if count > best_count:
            best_count = count
            best_delim = delim
    return best_delim


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def read_rows_from_text(text: str) -> List[List[str]]:
    if not text:
        return []
    delim = detect_delimiter(text[:1000])
    reader = csv.reader(text.splitlines(), delimiter=delim)
    return [[cell.strip() for cell in row] for row in reader if any(c.strip() for c in row)]


def read_rows(path: Path) -> List[List[str]]:
    text = read_text(path)
    return read_rows_from_text(text)


def parse_meta_rows(rows: List[List[str]]) -> Dict[str, str]:
    if not rows:
        raise ValueError("Metadata table is empty.")

    # Support both with and without header row.
    start_idx = 0
    if len(rows[0]) >= 2 and rows[0][0].lower() in {"key", "field"}:
        start_idx = 1

    meta: Dict[str, str] = {}
    for row in rows[start_idx:]:
        if len(row) < 2:
            continue
        key = row[0].strip().lower()
        value = row[1].strip()
        if key:
            meta[key] = value

    if not meta:
        raise ValueError("Could not parse metadata key/value rows.")

    return meta


def parse_meta(path: Path) -> Dict[str, str]:
    return parse_meta_rows(read_rows(path))


def parse_decimal(value: str, field_name: str) -> Decimal:
    cleaned = value.replace(" ", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid number for {field_name}: '{value}'") from exc


def parse_items_rows(rows: List[List[str]]) -> List[Item]:
    if len(rows) < 2:
        raise ValueError("Items table must include header row + at least 1 item row.")

    headers = [h.strip().lower() for h in rows[0]]
    required = {"description", "quantity", "unit_price"}
    missing = required - set(headers)
    if missing:
        raise ValueError(f"Items table missing required columns: {', '.join(sorted(missing))}")

    idx = {name: headers.index(name) for name in headers}
    items: List[Item] = []

    for row in rows[1:]:
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        desc = row[idx["description"]].strip()
        if not desc:
            continue
        qty = parse_decimal(row[idx["quantity"]], "quantity")
        unit_price = parse_decimal(row[idx["unit_price"]], "unit_price")
        tax_rate_value = row[idx["tax_rate"]].strip() if "tax_rate" in idx else "0"
        tax_rate = parse_decimal(tax_rate_value or "0", "tax_rate")
        items.append(Item(description=desc, quantity=qty, unit_price=unit_price, tax_rate=tax_rate))

    if not items:
        raise ValueError("No valid item rows found.")

    return items


def parse_items(path: Path) -> List[Item]:
    return parse_items_rows(read_rows(path))


def money(value: Decimal, currency: str) -> str:
    return f"{currency} {value.quantize(Decimal('0.01'))}"


def safe_get(meta: Dict[str, str], key: str, default: str = "") -> str:
    return meta.get(key, default)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def branding_color(branding: Dict[str, str]) -> str:
    return branding.get("accent_color") or branding.get("primary_color_hex") or "#2A5CAA"


def compute_totals(items: List[Item]) -> Tuple[Decimal, Decimal, Decimal]:
    subtotal = sum((i.net_total for i in items), Decimal("0"))
    tax_total = sum((i.tax_amount for i in items), Decimal("0"))
    grand_total = subtotal + tax_total
    return subtotal, tax_total, grand_total


def header_footer(canvas, doc, branding: Dict[str, str]):
    canvas.saveState()
    width, height = A4

    # Header line
    canvas.setStrokeColor(colors.HexColor(branding_color(branding)))
    canvas.setLineWidth(1)
    canvas.line(15 * mm, height - 20 * mm, width - 15 * mm, height - 20 * mm)

    # Footer text
    footer = branding.get("footer_text", "")
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(15 * mm, 12 * mm, footer[:180])
    canvas.restoreState()


def build_document(
    output_path: Path,
    doc_type_title: str,
    meta: Dict[str, str],
    items: List[Item],
    branding: Dict[str, str],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=22 * mm,
        bottomMargin=20 * mm,
    )

    story = []

    # Top section: logo + company name
    logo_path = branding.get("logo_path", "")
    logo_exists = logo_path and os.path.exists(logo_path)
    title_color = colors.HexColor(branding_color(branding))

    if logo_exists:
        story.append(Image(logo_path, width=35 * mm, height=18 * mm))
        story.append(Spacer(1, 3 * mm))

    company_name = branding.get("company_name", "Company")
    story.append(Paragraph(f"<b>{company_name}</b>", styles["Title"]))
    story.append(Paragraph(branding.get("company_address", ""), styles["Normal"]))
    story.append(Spacer(1, 5 * mm))

    # Document title
    title_style = styles["Heading1"].clone("docTitle")
    title_style.textColor = title_color
    story.append(Paragraph(doc_type_title, title_style))
    story.append(Spacer(1, 4 * mm))

    # Metadata table
    meta_rows = [
        ["Document No.", safe_get(meta, "document_number", "N/A")],
        ["Date", safe_get(meta, "date", utc_now().date().isoformat())],
        ["Customer", safe_get(meta, "customer_name", "")],
        ["Customer Email", safe_get(meta, "customer_email", "")],
        ["Customer Address", safe_get(meta, "customer_address", "")],
    ]

    meta_tbl = Table(meta_rows, colWidths=[35 * mm, 145 * mm])
    meta_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(meta_tbl)
    story.append(Spacer(1, 6 * mm))

    currency = safe_get(meta, "currency", "EUR")

    # Items table
    item_rows = [["Description", "Qty", "Unit Price", "Tax", "Line Total"]]
    for item in items:
        item_rows.append(
            [
                item.description,
                str(item.quantity),
                money(item.unit_price, currency),
                f"{(item.tax_rate * Decimal('100')).quantize(Decimal('0.01'))}%",
                money(item.gross_total, currency),
            ]
        )

    item_tbl = Table(item_rows, colWidths=[85 * mm, 18 * mm, 28 * mm, 20 * mm, 29 * mm])
    item_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(branding_color(branding))),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(item_tbl)
    story.append(Spacer(1, 5 * mm))

    subtotal, tax_total, grand_total = compute_totals(items)
    totals_rows = [
        ["Subtotal", money(subtotal, currency)],
        ["Tax", money(tax_total, currency)],
        ["Total", money(grand_total, currency)],
    ]

    totals_tbl = Table(totals_rows, colWidths=[35 * mm, 35 * mm], hAlign="RIGHT")
    totals_tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.whitesmoke),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    story.append(totals_tbl)
    story.append(Spacer(1, 5 * mm))

    notes = safe_get(meta, "notes", "")
    if notes:
        story.append(Paragraph(f"<b>Notes:</b> {notes}", styles["Normal"]))

    doc.build(story, onFirstPage=lambda c, d: header_footer(c, d, branding), onLaterPages=lambda c, d: header_footer(c, d, branding))


def load_branding(path: Path) -> Dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate order confirmation + proforma PDFs from pasted table data.")
    meta_group = parser.add_mutually_exclusive_group(required=True)
    meta_group.add_argument("--meta", help="Path to metadata table file")
    meta_group.add_argument("--meta-text", help="Raw metadata table text (copy/paste)")
    items_group = parser.add_mutually_exclusive_group(required=True)
    items_group.add_argument("--items", help="Path to items table file")
    items_group.add_argument("--items-text", help="Raw items table text (copy/paste)")
    parser.add_argument("--branding", required=True, help="Path to branding JSON config")
    parser.add_argument("--output-dir", default="output", help="Output directory for PDFs")
    args = parser.parse_args()

    branding_path = Path(args.branding)
    out_dir = Path(args.output_dir)

    if args.meta_text:
        meta = parse_meta_rows(read_rows_from_text(args.meta_text.strip()))
    else:
        meta = parse_meta(Path(args.meta))

    if args.items_text:
        items = parse_items_rows(read_rows_from_text(args.items_text.strip()))
    else:
        items = parse_items(Path(args.items))

    branding = load_branding(branding_path)

    doc_no = safe_get(meta, "document_number", utc_now().strftime("%Y%m%d%H%M%S"))
    order_path = out_dir / f"order_confirmation_{doc_no}.pdf"
    proforma_path = out_dir / f"proforma_{doc_no}.pdf"

    build_document(order_path, "Order Confirmation", meta, items, branding)
    build_document(proforma_path, "Proforma Invoice", meta, items, branding)

    print(f"Generated: {order_path}")
    print(f"Generated: {proforma_path}")


if __name__ == "__main__":
    main()
