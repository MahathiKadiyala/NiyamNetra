# Statutory Rule Coverage & Operational Limitations

**Document Code:** `DOC-12`  
**Active Production Baseline:** Rule Pack `2026.09.v1` (26 Rules Registered: 19 Executable Checks, 7 Manual-Review/Advisory Checks)  
**Legal Framework:** Legal Metrology (Packaged Commodities) Rules, 2011  

---

## 1. What NiyamNetra Actually Automates

To ensure credibility with enforcement authorities, NiyamNetra explicitly documents its statutory automation scope. The platform does not claim to automate "all of legal metrology"; it provides automated decision-support across **26 versioned statutory rules: 19 executable algorithmic checks and 7 manual-review/advisory checks**:

```
                              ┌────────────────────────────────────────┐
                              │      26 Registered Rules Breakdown     │
                              └────────────────────────────────────────┘
                                                  │
         ┌─────────────────────────┬──────────────┴───────────┬─────────────────────────┐
         ▼                         ▼                          ▼                         ▼
   Core Declarations      Visual & Typography        Packaging & Sizing        E-Commerce & Digital
   (CHK01, CHK04,         (CHK06, CHK06b,            (CHK02, CHK03,            (CHK15, CHK16)
    CHK05, CHK11,          CHK07, CHK08,              CHK10, CHK12,
    CHK19, CHK20)          CHK09, CHK22)              CHK13, CHK14)
```

### 1.1 Automated Core Declaration Checks
- **Mandatory Fields Presence (`CHK01`):** Detects manufacturer/packer/importer name and address, commodity name, net quantity, MRP, packing date, and consumer care info under Rule 6(1).
- **Date Formatting & Shelf-Life (`CHK04`):** Verifies standard date formats (`MM/YYYY`) and flags expired dates under Rule 6(1)(e) & Rule 2(m).
- **Standard Metric Units (`CHK05`):** Strictly verifies metric units (`g`, `kg`, `ml`, `L`, `m`, `cm`, `N`, `U`) under Rules 12 & 13. Rejects illegal symbols (`gms`, `kilos`, `litres`, `cc`).
- **Address Completeness (`CHK11`):** Verifies presence of postal PIN code and city in manufacturer declarations.
- **Consumer Care Details (`CHK19`):** Verifies presence of telephone, email, and postal contact under Rule 6(1)(g).
- **Country of Origin (`CHK20`):** Enforces country of origin declarations on imported commodities under Rule 6(1)(m).

### 1.2 Automated Visual & Typography Checks
- **Numeral & Character Height (`CHK06` & `CHK06b`):** Mathematically maps Principal Display Panel area ($A$) to minimum character height under **Rule 7 Table-I** (1.0 mm to 6.0 mm) via `CHK06`, while `CHK06b` evaluates the Net Quantity declaration's specific minimum numeral height keyed to quantity.
- **Letter Height of Accompanying Units (`CHK07`):** Verifies unit symbol height proportionality under Rule 7(3).
- **Declaration Grouping & Prominence (`CHK08`, `CHK09`, `CHK22`):** Checks grouping on PDP and background contrast under Rules 8 & 9.

### 1.3 Anti-Evasion & Multi-Panel Consistency Checks
- **Unit Sale Price (`CHK12`):** Validates Unit Sale Price (USP) per g/kg/ml/L under Rule 6(1)(aa).
- **Sticker Regulations (`CHK13`):** Flags unauthorized price stickers under Rule 6(1)(da); verifies sticker lowers price without obscuring original markings.
- **Dual Pricing Prohibition (`CHK14`):** Detects dual pricing across different panels under Rule 2(h) Proviso & Rule 6(2A).
- **Standard Packaging Quantities (`CHK10`):** Checks standard commodity sizes under Rule 5 and the Second Schedule.

### 1.4 E-Commerce Digital Marketplaces
- **Marketplace Disclosures (`CHK15`):** Verifies mandatory declaration display on digital product listings under Rule 6(10).
- **Origin & Manufacturer Filter (`CHK16`):** Verifies country of origin and seller identity on e-commerce platforms under Rule 6(10A).

---

## 2. Statutory Manual Review Boundaries

Certain legal provisions involve ongoing statutory or administrative ambiguity. Rather than risking false algorithmic prosecutions, NiyamNetra establishes formal manual review boundaries:

### 2.1 Fourth Amendment Origin-Marking (Rule 6(4A)(d))
- **Statutory Context:** G.S.R. 826(E) dated 21.09.2026 inserted clause (d) into Rule 6(4A), introducing origin-marking symbols for soaps, shampoos, toothpastes, cosmetics, and toiletries.
- **Legal Ambiguity:** The introductory phrasing of parent Rule 6(4A) is permissive (*"Nothing in these rules shall preclude..."*). Judicial enforcement of symbol omission under Section 36(1) remains unclarified.
- **System Behavior:** Missing symbols on these commodities produce **`REVIEW_REQUIRED` (Manual Officer Review)** rather than an automated violation verdict.

### 2.2 Uncalibrated Scale References
- **Requirement:** Numeral height verification under Rule 7 Table-I requires an optical reference standard.
- **System Behavior:** If no calibration standard is detected, the check returns `not_assessed` with a prompt:  
  *`CAP_CALIBRATION_REQUIRED` — "Place standard ID card or coin alongside packaging."*

---

## 3. Physical & Technical Limitations

Enforcement officers must understand the physical constraints of optical scanning:

| Constraint | Physical Scenario | Engine Limitation & Mitigation |
|---|---|---|
| **Severe Specular Glare** | Highly reflective metallic foil or glossy pouches under direct lighting. | Blind spots over text. Quality gate rejects images with glare > 15%; requests angled capture. |
| **Curved Cylindrical Distortion** | Tall beverage cans or medicine bottles photographed from an angle. | Characters along perimeter undergo severe non-linear perspective compression. Requires multi-panel unrolled capture. |
| **Transparent / See-Through Packaging** | Clear plastic pouches where contents create chaotic background patterns. | Bounding box contrast heuristic may return `not_assessed` for low contrast. |
| **Physical Package Deformation** | Crushed, wrinkled, or torn packages in transport godowns. | Homography quad detection fails. Inspector must flatten packaging manually or enter dimensions manually. |
| **Chemical Adulteration & Fill Volume** | Short weight, slack fill, chemical purity. | **Completely out of scope.** NiyamNetra verifies labeling declarations; physical weighing requires standard test weights. |
| **Non-Metric Regional Dialects** | Handwritten local-dialect declarations on rural packaging. | OCR and LLM support English and Hindi. Regional scripts fall back to `not_assessed` for manual officer review. |
