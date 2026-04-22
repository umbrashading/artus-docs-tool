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
import html
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
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
    details: str = ""

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
    raw = value.strip()
    number_match = re.search(r"-?\d+(?:[.,]\d+)?", raw)
    if not number_match:
        raise ValueError(f"Invalid number for {field_name}: '{value}'")
    cleaned = number_match.group(0).replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid number for {field_name}: '{value}'") from exc


def parse_amount_currency(value: str) -> Tuple[Decimal | None, str]:
    amount = None
    currency = ""
    number_match = re.search(r"-?\d+(?:[.,]\d+)?", value)
    if number_match:
        try:
            amount = Decimal(number_match.group(0).replace(",", "."))
        except InvalidOperation:
            amount = None
        suffix = value[number_match.end() :].strip()
        currency_match = re.search(r"[A-Za-z]{3}", suffix)
        if currency_match:
            currency = currency_match.group(0).upper()
    return amount, currency


def normalize_key(value: str) -> str:
    lowered = value.strip().lower()
    collapsed = re.sub(r"\s+", "_", lowered)
    return re.sub(r"[^a-z0-9_]", "", collapsed)


def extract_document_number(order_value: str) -> str:
    bracket_match = re.search(r"\[([^\]]+)\]", order_value)
    if bracket_match:
        return bracket_match.group(1).strip()
    return order_value.strip()


def read_label_value(lines: List[str], index: int) -> Tuple[Tuple[str, str] | None, int]:
    line = lines[index].strip()
    match = re.match(r"^([^:]+):\s*(.*)$", line)
    if not match:
        return None, index + 1

    key = normalize_key(match.group(1))
    value = match.group(2).strip()
    next_index = index + 1
    if value:
        return (key, value), next_index

    while next_index < len(lines) and not lines[next_index].strip():
        next_index += 1
    if next_index < len(lines):
        candidate = lines[next_index].strip()
        if ":" not in candidate:
            return (key, candidate), next_index + 1

    return (key, value), next_index


def parse_order_text(order_text: str) -> Tuple[Dict[str, str], List[Item]]:
    lines = order_text.splitlines()
    meta_pairs: List[Tuple[str, str]] = []
    raw_items: List[Dict[str, str]] = []
    current_item: Dict[str, str] | None = None
    idx = 0

    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            idx += 1
            continue

        item_match = re.match(r"(?i)^item\s*:\s*(.*)$", stripped)
        if item_match:
            if current_item is not None:
                raw_items.append(current_item)
            current_item = {}
            item_number = item_match.group(1).strip()
            if item_number:
                current_item["item_number"] = item_number
            idx += 1
            continue

        pair, next_idx = read_label_value(lines, idx)
        if pair is None:
            idx += 1
            continue

        key, value = pair
        if current_item is None:
            meta_pairs.append((key, value))
        else:
            current_item[key] = value
        idx = next_idx

    if current_item is not None:
        raw_items.append(current_item)

    meta: Dict[str, str] = {}
    for key, value in meta_pairs:
        if value:
            meta[key] = value

    if not meta and not raw_items:
        raise ValueError("Could not parse any fields from order text.")

    if "order" in meta and "document_number" not in meta:
        meta["document_number"] = extract_document_number(meta["order"])
    if "client" in meta and "customer_name" not in meta:
        meta["customer_name"] = meta["client"]
    if "address" in meta and "customer_address" not in meta:
        meta["customer_address"] = meta["address"]

    order_total = meta.get("total_cost", "")
    if order_total:
        amount, currency = parse_amount_currency(order_total)
        if amount is not None:
            meta["reported_total_cost"] = str(amount)
        if currency and "currency" not in meta:
            meta["currency"] = currency
    if "currency" not in meta:
        meta["currency"] = "GBP"

    items: List[Item] = []
    for item_index, raw_item in enumerate(raw_items, start=1):
        quantity = Decimal("1")
        if raw_item.get("quantity"):
            quantity = parse_decimal(raw_item["quantity"], "quantity")
        if quantity == 0:
            quantity = Decimal("1")

        if raw_item.get("unit_price"):
            unit_price = parse_decimal(raw_item["unit_price"], "unit_price")
        elif raw_item.get("total_cost"):
            total_cost = parse_decimal(raw_item["total_cost"], "total_cost")
            unit_price = total_cost / quantity
        else:
            unit_price = Decimal("0")

        tax_rate = Decimal("0")
        if raw_item.get("tax_rate"):
            tax_rate = parse_decimal(raw_item["tax_rate"], "tax_rate")

        reference = raw_item.get("reference", "").strip()
        product_id = raw_item.get("productid", "").strip()
        description = " - ".join(part for part in [reference, product_id] if part)
        if not description:
            description = f"Item {raw_item.get('item_number', str(item_index))}"

        detail_order = [
            "install",
            "mount",
            "colour",
            "stile",
            "frame",
            "frame_sides",
            "framesides",
            "louvre",
            "width",
            "height",
            "sqm",
        ]
        details: List[str] = []
        if raw_item.get("item_number"):
            details.append(f"Item {raw_item['item_number']}")
        for key in detail_order:
            value = raw_item.get(key, "").strip()
            if not value:
                continue
            details.append(f"{key.replace('_', ' ').title()}: {value}")

        items.append(
            Item(
                description=description,
                quantity=quantity,
                unit_price=unit_price,
                tax_rate=tax_rate,
                details=", ".join(details),
            )
        )

    if not items:
        raise ValueError("No item blocks found in order text.")

    return meta, items


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


def format_doc_date(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y")


def branding_primary_color(branding: Dict[str, str]) -> str:
    return branding.get("primary_color_hex") or "#004225"


def branding_accent_color(branding: Dict[str, str]) -> str:
    return branding.get("accent_color") or "#CCBF9E"


def compute_totals(items: List[Item]) -> Tuple[Decimal, Decimal, Decimal]:
    subtotal = sum((i.net_total for i in items), Decimal("0"))
    tax_total = sum((i.tax_amount for i in items), Decimal("0"))
    grand_total = subtotal + tax_total
    return subtotal, tax_total, grand_total


def paragraph_escape(value: str) -> str:
    return html.escape(value, quote=True)


def logo_dimensions(logo_path: str, max_width_mm: float) -> Tuple[float, float]:
    max_width = max_width_mm * mm
    try:
        reader = ImageReader(logo_path)
        original_width, original_height = reader.getSize()
        if not original_width or not original_height:
            return max_width, 12 * mm
        height = (max_width * float(original_height)) / float(original_width)
        return max_width, height
    except Exception:
        return max_width, 12 * mm


def footer_lines_for_document(doc_type_title: str, branding: Dict[str, str]) -> List[str]:
    common_footer = [
        "Spectrum Supply t/a Umbra Shading, 31 Ystrad Road, Fforestfach, Swansea, SA5 4BT.",
        "01792 562015 sales@umbrashading.co.uk.",
        "Company Registration No. 7317206.",
        "The above pricing excludes VAT.",
    ]
    if doc_type_title.lower().startswith("proforma"):
        common_footer.extend(
            [
                "Account Name: Spectrum Supply Ltd",
                "Sort Code: 12-20-26",
                "Account Number: 01874691",
            ]
        )
    return common_footer


def header_footer(canvas, doc, branding: Dict[str, str], doc_type_title: str):
    canvas.saveState()
    width, height = A4

    # Header line
    canvas.setStrokeColor(colors.HexColor(branding_primary_color(branding)))
    canvas.setLineWidth(1)
    canvas.line(15 * mm, height - 20 * mm, width - 15 * mm, height - 20 * mm)

    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor(branding_primary_color(branding)))
    footer_lines = footer_lines_for_document(doc_type_title, branding)
    y = 10 * mm
    for line in footer_lines:
        canvas.drawString(15 * mm, y, line[:190])
        y += 3.8 * mm
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
    primary_color = colors.HexColor(branding_primary_color(branding))
    accent_color = colors.HexColor(branding_accent_color(branding))

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=22 * mm,
        bottomMargin=42 * mm,
    )

    story = []
    now = utc_now()
    is_proforma = doc_type_title.lower().startswith("proforma")
    proforma_vat_rate = Decimal("0.20")

    # Header content section: logo, title, UMA reference (+ dates for proforma)
    logo_path = branding.get("logo_path", "")
    logo_exists = logo_path and os.path.exists(logo_path)

    if logo_exists:
        logo_width, logo_height = logo_dimensions(logo_path, max_width_mm=64)
        story.append(Image(logo_path, width=logo_width, height=logo_height))
        story.append(Spacer(1, 3 * mm))

    # Document title
    title_style = styles["Heading1"].clone("docTitle")
    title_style.textColor = primary_color
    story.append(Paragraph(paragraph_escape(doc_type_title), title_style))
    doc_ref = safe_get(meta, "document_number", "N/A")
    subheading_style = styles["Heading3"].clone("docRef")
    subheading_style.textColor = primary_color
    story.append(Paragraph(f"UMA Order Reference: <b>{paragraph_escape(doc_ref)}</b>", subheading_style))
    if doc_type_title.lower().startswith("proforma"):
        date_str = format_doc_date(now)
        story.append(Paragraph(f"Invoice Date: <b>{date_str}</b>", styles["Normal"]))
        story.append(Paragraph(f"Due Date: <b>{date_str}</b>", styles["Normal"]))
    story.append(Spacer(1, 5 * mm))

    # Metadata table
    meta_rows = [
        ["Document No.", safe_get(meta, "document_number", "N/A")],
        ["Date", safe_get(meta, "date", format_doc_date(now))],
        ["Customer", safe_get(meta, "customer_name", "")],
        ["Status", safe_get(meta, "status", "")],
        ["Reseller", safe_get(meta, "reseller", "")],
        ["Agent", safe_get(meta, "agent", "")],
        ["Customer Address", safe_get(meta, "customer_address", "")],
    ]
    if safe_get(meta, "customer_email", ""):
        meta_rows.append(["Customer Email", safe_get(meta, "customer_email", "")])

    meta_tbl = Table(meta_rows, colWidths=[35 * mm, 145 * mm])
    meta_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), accent_color),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8D8D8")),
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
        line_tax_rate = proforma_vat_rate if is_proforma else item.tax_rate
        line_total = item.net_total * (Decimal("1") + line_tax_rate)
        description_cell = paragraph_escape(item.description)
        if item.details:
            details_markup = paragraph_escape(item.details)
            description_cell = (
                f"{description_cell}<br/>"
                f"<font size='7' color='#666666'>{details_markup}</font>"
            )
        item_rows.append(
            [
                Paragraph(description_cell, styles["Normal"]),
                str(item.quantity),
                money(item.unit_price, currency),
                f"{(line_tax_rate * Decimal('100')).quantize(Decimal('0.01'))}%",
                money(line_total, currency),
            ]
        )

    item_tbl = Table(item_rows, colWidths=[85 * mm, 18 * mm, 28 * mm, 20 * mm, 29 * mm])
    item_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), primary_color),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8D8D8")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F4EC")]),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(item_tbl)
    story.append(Spacer(1, 5 * mm))

    if is_proforma:
        subtotal = sum((i.net_total for i in items), Decimal("0"))
        tax_total = subtotal * proforma_vat_rate
        grand_total = subtotal + tax_total
        tax_label = "VAT (20%)"
    else:
        subtotal, tax_total, grand_total = compute_totals(items)
        tax_label = "Tax"

    totals_rows = [
        ["Subtotal", money(subtotal, currency)],
        [tax_label, money(tax_total, currency)],
        ["Total", money(grand_total, currency)],
    ]

    totals_tbl = Table(totals_rows, colWidths=[35 * mm, 35 * mm], hAlign="RIGHT")
    totals_tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8D8D8")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, -1), accent_color),
                ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor("#0F281F")),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    story.append(totals_tbl)
    story.append(Spacer(1, 5 * mm))

    if doc_type_title.lower().startswith("proforma"):
        bank_rows = [
            ["BANK DETAILS", ""],
            ["Account Name", "Spectrum Supply Ltd"],
            ["Sort Code", "12-20-26"],
            ["Account Number", "01874691"],
        ]
        bank_tbl = Table(bank_rows, colWidths=[40 * mm, 110 * mm])
        bank_tbl.setStyle(
            TableStyle(
                [
                    ("SPAN", (0, 0), (1, 0)),
                    ("BACKGROUND", (0, 0), (1, 0), primary_color),
                    ("TEXTCOLOR", (0, 0), (1, 0), colors.white),
                    ("FONTNAME", (0, 0), (1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                    ("BACKGROUND", (0, 1), (0, -1), accent_color),
                    ("GRID", (0, 0), (1, -1), 0.4, colors.HexColor("#D8D8D8")),
                    ("FONTSIZE", (0, 0), (1, -1), 10),
                    ("VALIGN", (0, 0), (1, -1), "MIDDLE"),
                ]
            )
        )
        story.append(bank_tbl)
        story.append(Spacer(1, 4 * mm))

    reported_total_cost = safe_get(meta, "reported_total_cost", "")
    if reported_total_cost:
        try:
            reported_total_amount = Decimal(reported_total_cost)
            reference_total = subtotal if is_proforma else grand_total
            if reported_total_amount != reference_total:
                difference = reference_total - reported_total_amount
                reference_label = "subtotal" if is_proforma else "total"
                note = (
                    f"System {reference_label} differs from order text total by "
                    f"{money(difference, currency)} (order text: {money(reported_total_amount, currency)})."
                )
                story.append(Paragraph(paragraph_escape(note), styles["Italic"]))
                story.append(Spacer(1, 3 * mm))
        except InvalidOperation:
            pass

    notes = safe_get(meta, "notes", "")
    if notes:
        story.append(Paragraph(f"<b>Notes:</b> {paragraph_escape(notes)}", styles["Normal"]))

    doc.build(
        story,
        onFirstPage=lambda c, d: header_footer(c, d, branding, doc_type_title),
        onLaterPages=lambda c, d: header_footer(c, d, branding, doc_type_title),
    )


def load_branding(path: Path) -> Dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate order confirmation + proforma PDFs from pasted table data.")
    parser.add_argument("--order-text", help="Raw order block text containing metadata and Item sections")
    parser.add_argument("--order-file", help="Path to text file containing raw order block")
    meta_group = parser.add_mutually_exclusive_group(required=False)
    meta_group.add_argument("--meta", help="Path to metadata table file")
    meta_group.add_argument("--meta-text", help="Raw metadata table text (copy/paste)")
    items_group = parser.add_mutually_exclusive_group(required=False)
    items_group.add_argument("--items", help="Path to items table file")
    items_group.add_argument("--items-text", help="Raw items table text (copy/paste)")
    parser.add_argument("--branding", required=True, help="Path to branding JSON config")
    parser.add_argument("--output-dir", default="output", help="Output directory for PDFs")
    args = parser.parse_args()

    branding_path = Path(args.branding)
    out_dir = Path(args.output_dir)

    if args.order_text or args.order_file:
        if args.order_text and args.order_file:
            raise ValueError("Use only one of --order-text or --order-file.")
        order_raw_text = args.order_text
        if args.order_file:
            order_raw_text = read_text(Path(args.order_file))
        meta, items = parse_order_text((order_raw_text or "").strip())
    else:
        if not (args.meta or args.meta_text):
            raise ValueError("Provide either --order-text or one of --meta / --meta-text.")
        if not (args.items or args.items_text):
            raise ValueError("Provide either --order-text or one of --items / --items-text.")

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
