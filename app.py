#!/usr/bin/env python3
from __future__ import annotations

import io
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Tuple

import streamlit as st

from generate_pdfs import build_document, load_branding, parse_order_text, safe_get, utc_now


def _is_authorized() -> bool:
    required_password = os.environ.get("APP_PASSWORD", "").strip()
    if not required_password:
        return True

    st.sidebar.header("Access")
    entered = st.sidebar.text_input("Password", type="password")
    if entered == required_password:
        return True
    if entered:
        st.sidebar.error("Incorrect password")
    else:
        st.sidebar.info("Enter password to continue")
    return False


def _logo_preview(branding: dict) -> None:
    logo_path = branding.get("logo_path", "")
    if logo_path and Path(logo_path).exists():
        st.image(logo_path, width=220)
    else:
        st.caption("Logo not found at configured path. PDFs will still generate.")


def _build_pdfs(order_text: str, branding_path: Path) -> Tuple[bytes, bytes, str]:
    meta, items = parse_order_text(order_text.strip())
    branding = load_branding(branding_path)
    doc_no = safe_get(meta, "document_number", utc_now().strftime("%Y%m%d%H%M%S"))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        order_pdf = tmp / f"order_confirmation_{doc_no}.pdf"
        proforma_pdf = tmp / f"proforma_{doc_no}.pdf"

        build_document(order_pdf, "Order Confirmation", meta, items, branding)
        build_document(proforma_pdf, "Proforma Invoice", meta, items, branding)

        order_bytes = order_pdf.read_bytes()
        proforma_bytes = proforma_pdf.read_bytes()
        return order_bytes, proforma_bytes, doc_no


def main() -> None:
    st.set_page_config(page_title="Artus Docs Tool", layout="wide")
    st.title("Artus Docs Tool")
    st.caption("Paste UMA order text and generate Order Confirmation + Proforma Invoice PDFs.")

    if not _is_authorized():
        st.stop()

    branding_path = Path("branding.example.json")
    if not branding_path.exists():
        st.error("branding.example.json not found.")
        st.stop()

    branding = load_branding(branding_path)

    if "generated_pdfs" not in st.session_state:
        st.session_state.generated_pdfs = None

    col1, col2 = st.columns([2, 1])
    with col1:
        st.subheader("Input")
        order_text = st.text_area(
            "Paste full order block",
            value="",
            height=420,
            placeholder="Order:\n[UMA-XXXX] ...\n\nDate:\n...",
        )
        generate = st.button("Generate PDFs", type="primary")

    with col2:
        st.subheader("Branding")
        _logo_preview(branding)
        st.json(
            {
                "primary_color_hex": branding.get("primary_color_hex"),
                "accent_color": branding.get("accent_color"),
                "logo_path": branding.get("logo_path"),
            }
        )

    if generate:
        if not order_text.strip():
            st.error("Paste order text before generating.")
            st.stop()

        try:
            order_bytes, proforma_bytes, doc_no = _build_pdfs(order_text, branding_path)
        except Exception as exc:  # basic internal tool; return parser/render errors directly
            st.error(f"Failed to generate PDFs: {exc}")
            st.stop()

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state.generated_pdfs = {
            "order_bytes": order_bytes,
            "proforma_bytes": proforma_bytes,
            "doc_no": doc_no,
            "timestamp": timestamp,
        }

    generated_pdfs = st.session_state.generated_pdfs
    if generated_pdfs:
        st.success(f"Generated successfully at {generated_pdfs['timestamp']}")

        d1, d2 = st.columns(2)
        with d1:
            st.download_button(
                "Download Order Confirmation",
                data=io.BytesIO(generated_pdfs["order_bytes"]),
                file_name=f"order_confirmation_{generated_pdfs['doc_no']}.pdf",
                mime="application/pdf",
            )
        with d2:
            st.download_button(
                "Download Proforma Invoice",
                data=io.BytesIO(generated_pdfs["proforma_bytes"]),
                file_name=f"proforma_{generated_pdfs['doc_no']}.pdf",
                mime="application/pdf",
            )


if __name__ == "__main__":
    main()
