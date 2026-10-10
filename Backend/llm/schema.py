"""Backend/llm/schema.py — Strict Structured Declaration Schema & Provenance Contracts (Phase 3).

Follows Core Principles (Sections 2, 6, 7, 8, 10, 11, 14, 15):
- Evidence-grounded declarations only.
- Strict provenance attached to every field (panel, image_id, text, bbox, confidence).
- Distinguishes confirmed, ambiguous, not_observed, and unassessable.
- Explicit candidate collections when OCR is ambiguous or multiple values exist.
- Separates OCR confidence from LLM confidence.
- Zero legal judgments or physical font-size decisions.
- Bridge method `to_extracted_fields()` preserves 100% downstream compatibility with rules_engine.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from ocr_engine import ExtractedField

# Declaration Status Constants (Section 15)
STATUS_CONFIRMED = "confirmed"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_NOT_OBSERVED = "not_observed"
STATUS_UNASSESSABLE = "unassessable"

# Overall LLM Result Status
LLM_STATUS_SUCCESS = "SUCCESS"
LLM_STATUS_AMBIGUOUS = "AMBIGUOUS"
LLM_STATUS_PENDING = "PENDING"
LLM_STATUS_UNAVAILABLE = "UNAVAILABLE"
LLM_STATUS_FAILED = "FAILED"

PROMPT_VERSION = "2026.03.v1"
SCHEMA_VERSION = "2026.03.v1"


@dataclass(slots=True)
class FieldProvenance:
    """Audit provenance tracking the source of an extracted declaration (Section 7)."""
    source_panel: str | None = None
    source_image_id: int | None = None
    source_text: str | None = None
    source_bbox: list[list[float]] | None = None  # [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
    ocr_confidence: float | None = None
    llm_confidence: float | None = None
    notes: str | None = None
    source_type: str | None = None  # ocr | llm_extraction | image_evidence | operator_input | inspector_confirmation | ecommerce_listing


@dataclass(slots=True)
class CandidateItem:
    """Alternative candidate value when OCR contains multiple values or ambiguity (Section 14)."""
    value: str | float | None = None
    raw_text: str = ""
    panel: str | None = None
    confidence: float | None = None
    notes: str | None = None


@dataclass(slots=True)
class MrpDeclaration:
    """Maximum Retail Price declaration under Rule 6(1)(e)."""
    status: str = STATUS_NOT_OBSERVED  # confirmed | ambiguous | not_observed | unassessable
    value: float | None = None
    currency: str = "INR"
    inclusive_of_taxes: bool | None = None
    raw_text: str | None = None
    candidates: list[CandidateItem] = field(default_factory=list)
    provenance: FieldProvenance | None = None


@dataclass(slots=True)
class NetQuantityDeclaration:
    """Net Quantity declaration under Rule 6(1)(f)."""
    status: str = STATUS_NOT_OBSERVED
    value: float | None = None
    unit: str | None = None
    normalized_value: float | None = None
    normalized_unit: str | None = None
    raw_text: str | None = None
    candidates: list[CandidateItem] = field(default_factory=list)
    provenance: FieldProvenance | None = None


@dataclass(slots=True)
class DateDeclaration:
    """Date declaration for manufacturing, packing, or expiry under Rule 6(1)(d) & Rule 6(1)(e)."""
    status: str = STATUS_NOT_OBSERVED
    raw_text: str | None = None
    normalized_date: str | None = None  # YYYY-MM or YYYY-MM-DD
    date_type_candidate: str | None = None  # mfg | packing | expiry | best_before | unknown
    provenance: FieldProvenance | None = None


@dataclass(slots=True)
class PartyDeclaration:
    """Manufacturer, packer, importer, or marketer declaration under Rule 6(1)(a) & 6(1)(aa)."""
    status: str = STATUS_NOT_OBSERVED
    name: str | None = None
    address: str | None = None
    party_type: str | None = None  # manufacturer | packer | importer | marketer | unknown
    raw_text: str | None = None
    provenance: FieldProvenance | None = None


@dataclass(slots=True)
class ConsumerCareDeclaration:
    """Consumer care details under Rule 6(1)(h)."""
    status: str = STATUS_NOT_OBSERVED
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    website: str | None = None
    raw_text: str | None = None
    provenance: FieldProvenance | None = None


@dataclass(slots=True)
class CommonFieldDeclaration:
    """Generic declaration field for commodity name, batch, country, etc."""
    status: str = STATUS_NOT_OBSERVED
    value: str | None = None
    raw_text: str | None = None
    provenance: FieldProvenance | None = None


@dataclass(slots=True)
class StructuredDeclarationResult:
    """Authoritative structured declaration payload for an inspection (Section 4 & 10)."""
    commodity_name: CommonFieldDeclaration = field(default_factory=CommonFieldDeclaration)
    mrp: MrpDeclaration = field(default_factory=MrpDeclaration)
    net_quantity: NetQuantityDeclaration = field(default_factory=NetQuantityDeclaration)
    dates: list[DateDeclaration] = field(default_factory=list)
    parties: list[PartyDeclaration] = field(default_factory=list)
    consumer_care: ConsumerCareDeclaration = field(default_factory=ConsumerCareDeclaration)
    country_of_origin: CommonFieldDeclaration = field(default_factory=CommonFieldDeclaration)
    batch_number: CommonFieldDeclaration = field(default_factory=CommonFieldDeclaration)
    unit_sale_price: CommonFieldDeclaration = field(default_factory=CommonFieldDeclaration)
    dimensions: CommonFieldDeclaration = field(default_factory=CommonFieldDeclaration)

    metadata: dict[str, Any] = field(default_factory=dict)
    raw_packaged_ocr: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize structured declaration result to JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredDeclarationResult:
        """Reconstruct StructuredDeclarationResult from a dictionary."""
        if not data:
            return cls()

        def _make_prov(p_data: dict | None) -> FieldProvenance | None:
            if not p_data:
                return None
            return FieldProvenance(
                source_panel=p_data.get("source_panel"),
                source_image_id=p_data.get("source_image_id"),
                source_text=p_data.get("source_text"),
                source_bbox=p_data.get("source_bbox"),
                ocr_confidence=p_data.get("ocr_confidence"),
                llm_confidence=p_data.get("llm_confidence"),
                notes=p_data.get("notes"),
                source_type=p_data.get("source_type"),
            )

        def _make_candidates(cand_list: list) -> list[CandidateItem]:
            out = []
            for c in cand_list or []:
                if isinstance(c, dict):
                    out.append(CandidateItem(
                        value=c.get("value"),
                        raw_text=c.get("raw_text", ""),
                        panel=c.get("panel"),
                        confidence=c.get("confidence"),
                        notes=c.get("notes"),
                    ))
            return out

        # Rehydrate MRP
        mrp_raw = data.get("mrp") or {}
        mrp = MrpDeclaration(
            status=mrp_raw.get("status", STATUS_NOT_OBSERVED),
            value=mrp_raw.get("value"),
            currency=mrp_raw.get("currency", "INR"),
            inclusive_of_taxes=mrp_raw.get("inclusive_of_taxes"),
            raw_text=mrp_raw.get("raw_text"),
            candidates=_make_candidates(mrp_raw.get("candidates", [])),
            provenance=_make_prov(mrp_raw.get("provenance")),
        )

        # Rehydrate Net Quantity
        qty_raw = data.get("net_quantity") or {}
        net_qty = NetQuantityDeclaration(
            status=qty_raw.get("status", STATUS_NOT_OBSERVED),
            value=qty_raw.get("value"),
            unit=qty_raw.get("unit"),
            normalized_value=qty_raw.get("normalized_value"),
            normalized_unit=qty_raw.get("normalized_unit"),
            raw_text=qty_raw.get("raw_text"),
            candidates=_make_candidates(qty_raw.get("candidates", [])),
            provenance=_make_prov(qty_raw.get("provenance")),
        )

        # Dates
        dates = []
        for d in data.get("dates") or []:
            if isinstance(d, dict):
                dates.append(DateDeclaration(
                    status=d.get("status", STATUS_NOT_OBSERVED),
                    raw_text=d.get("raw_text"),
                    normalized_date=d.get("normalized_date"),
                    date_type_candidate=d.get("date_type_candidate"),
                    provenance=_make_prov(d.get("provenance")),
                ))

        # Parties
        parties = []
        for p in data.get("parties") or []:
            if isinstance(p, dict):
                parties.append(PartyDeclaration(
                    status=p.get("status", STATUS_NOT_OBSERVED),
                    name=p.get("name"),
                    address=p.get("address"),
                    party_type=p.get("party_type"),
                    raw_text=p.get("raw_text"),
                    provenance=_make_prov(p.get("provenance")),
                ))

        # Consumer Care
        cc_raw = data.get("consumer_care") or {}
        consumer_care = ConsumerCareDeclaration(
            status=cc_raw.get("status", STATUS_NOT_OBSERVED),
            phone=cc_raw.get("phone"),
            email=cc_raw.get("email"),
            address=cc_raw.get("address"),
            website=cc_raw.get("website"),
            raw_text=cc_raw.get("raw_text"),
            provenance=_make_prov(cc_raw.get("provenance")),
        )

        def _make_common(key: str) -> CommonFieldDeclaration:
            raw = data.get(key) or {}
            return CommonFieldDeclaration(
                status=raw.get("status", STATUS_NOT_OBSERVED),
                value=raw.get("value"),
                raw_text=raw.get("raw_text"),
                provenance=_make_prov(raw.get("provenance")),
            )

        return cls(
            commodity_name=_make_common("commodity_name"),
            mrp=mrp,
            net_quantity=net_qty,
            dates=dates,
            parties=parties,
            consumer_care=consumer_care,
            country_of_origin=_make_common("country_of_origin"),
            batch_number=_make_common("batch_number"),
            unit_sale_price=_make_common("unit_sale_price"),
            dimensions=_make_common("dimensions"),
            metadata=data.get("metadata", {}),
            raw_packaged_ocr=data.get("raw_packaged_ocr", ""),
        )

    def to_extracted_fields(self) -> dict[str, ExtractedField]:
        """Convert structured LLM declaration results into the ExtractedField contract.

        Guarantees 100% backward compatibility with rules_engine.py (Section 2 & 25).
        """
        out: dict[str, ExtractedField] = {}

        # 1. MRP
        if self.mrp.status == STATUS_CONFIRMED and self.mrp.value is not None:
            prov = self.mrp.provenance
            out["mrp"] = ExtractedField(
                value=f"{self.mrp.value:.2f}" if isinstance(self.mrp.value, (int, float)) else str(self.mrp.value),
                confidence=prov.ocr_confidence if prov else None,
                source_line=self.mrp.raw_text or (prov.source_text if prov else None),
                found=True,
            )
        else:
            out["mrp"] = ExtractedField(None, None, None, False)

        # 2. Net Quantity
        if self.net_quantity.status == STATUS_CONFIRMED and self.net_quantity.value is not None:
            prov = self.net_quantity.provenance
            val_str = str(int(self.net_quantity.value) if self.net_quantity.value == int(self.net_quantity.value) else self.net_quantity.value)
            out["net_quantity"] = ExtractedField(
                value=val_str,
                confidence=prov.ocr_confidence if prov else None,
                source_line=self.net_quantity.raw_text or (prov.source_text if prov else None),
                found=True,
            )
        else:
            out["net_quantity"] = ExtractedField(None, None, None, False)

        # 3. Manufacturer / Packer / Importer
        mfr_found = False
        mfr_party = next((p for p in self.parties if p.status == STATUS_CONFIRMED and p.name), None)
        if mfr_party:
            prov = mfr_party.provenance
            full_val = f"{mfr_party.name}, {mfr_party.address}" if mfr_party.address else mfr_party.name
            ef = ExtractedField(
                value=full_val,
                confidence=prov.ocr_confidence if prov else None,
                source_line=mfr_party.raw_text or (prov.source_text if prov else None),
                found=True,
            )
            out["manufacturer"] = ef
            out["packer"] = ef
            out["importer"] = ef
            mfr_found = True
        else:
            out["manufacturer"] = ExtractedField(None, None, None, False)
            out["packer"] = ExtractedField(None, None, None, False)
            out["importer"] = ExtractedField(None, None, None, False)

        # 4. Dates: mfg / packing & best_before / expiry
        mfg_date = next((d for d in self.dates if d.status == STATUS_CONFIRMED and d.date_type_candidate in ("mfg", "packing")), None)
        if mfg_date:
            prov = mfg_date.provenance
            out["date_of_manufacture"] = ExtractedField(
                value=mfg_date.normalized_date or mfg_date.raw_text,
                confidence=prov.ocr_confidence if prov else None,
                source_line=mfg_date.raw_text or (prov.source_text if prov else None),
                found=True,
            )
        else:
            out["date_of_manufacture"] = ExtractedField(None, None, None, False)

        exp_date = next((d for d in self.dates if d.status == STATUS_CONFIRMED and d.date_type_candidate in ("expiry", "best_before")), None)
        if exp_date:
            prov = exp_date.provenance
            out["best_before"] = ExtractedField(
                value=exp_date.normalized_date or exp_date.raw_text,
                confidence=prov.ocr_confidence if prov else None,
                source_line=exp_date.raw_text or (prov.source_text if prov else None),
                found=True,
            )
        else:
            out["best_before"] = ExtractedField(None, None, None, False)

        # 5. Consumer Care
        if self.consumer_care.status == STATUS_CONFIRMED and (self.consumer_care.phone or self.consumer_care.email or self.consumer_care.address):
            prov = self.consumer_care.provenance
            care_val = self.consumer_care.phone or self.consumer_care.email or self.consumer_care.address or ""
            out["consumer_care"] = ExtractedField(
                value=care_val,
                confidence=prov.ocr_confidence if prov else None,
                source_line=self.consumer_care.raw_text or (prov.source_text if prov else None),
                found=True,
            )
        else:
            out["consumer_care"] = ExtractedField(None, None, None, False)

        # 6. Commodity Name
        if self.commodity_name.status == STATUS_CONFIRMED and self.commodity_name.value:
            prov = self.commodity_name.provenance
            out["commodity"] = ExtractedField(
                value=self.commodity_name.value,
                confidence=prov.ocr_confidence if prov else None,
                source_line=self.commodity_name.raw_text or (prov.source_text if prov else None),
                found=True,
            )
        else:
            out["commodity"] = ExtractedField(None, None, None, False)

        # 7. Country of Origin
        if self.country_of_origin.status == STATUS_CONFIRMED and self.country_of_origin.value:
            prov = self.country_of_origin.provenance
            out["country_of_origin"] = ExtractedField(
                value=self.country_of_origin.value,
                confidence=prov.ocr_confidence if prov else None,
                source_line=self.country_of_origin.raw_text or (prov.source_text if prov else None),
                found=True,
            )
        else:
            out["country_of_origin"] = ExtractedField(None, None, None, False)

        # 8. Batch Number
        if self.batch_number.status == STATUS_CONFIRMED and self.batch_number.value:
            prov = self.batch_number.provenance
            out["batch_number"] = ExtractedField(
                value=self.batch_number.value,
                confidence=prov.ocr_confidence if prov else None,
                source_line=self.batch_number.raw_text or (prov.source_text if prov else None),
                found=True,
            )
        else:
            out["batch_number"] = ExtractedField(None, None, None, False)

        # 9. Unit Sale Price
        if self.unit_sale_price.status == STATUS_CONFIRMED and self.unit_sale_price.value:
            prov = self.unit_sale_price.provenance
            out["unit_sale_price"] = ExtractedField(
                value=self.unit_sale_price.value,
                confidence=prov.ocr_confidence if prov else None,
                source_line=self.unit_sale_price.raw_text or (prov.source_text if prov else None),
                found=True,
            )
        else:
            out["unit_sale_price"] = ExtractedField(None, None, None, False)

        # 10. Dimensions
        if self.dimensions.status == STATUS_CONFIRMED and self.dimensions.value:
            prov = self.dimensions.provenance
            out["dimensions"] = ExtractedField(
                value=self.dimensions.value,
                confidence=prov.ocr_confidence if prov else None,
                source_line=self.dimensions.raw_text or (prov.source_text if prov else None),
                found=True,
            )
        else:
            out["dimensions"] = ExtractedField(None, None, None, False)

        return out
