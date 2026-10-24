# Deterministic Legal Compliance Engine

**Document Code:** `DOC-05`  
**Applies to:** Core Rules Engine (`rules_engine.py` & `Backend/rules/`)  
**Active Production Baseline:** Rule Pack `2026.09.v1` (Rules As At: `2026-09-21`)  
**Primary Statutes:** Legal Metrology Act, 2009 & LM (Packaged Commodities) Rules, 2011  

---

## 1. Compliance Architecture & Statutory Safety Principles

The NiyamNetra Legal Compliance Engine is a strictly deterministic, versioned, date-aware expert system. It guarantees mathematical reproducibility and auditability for every evaluated pre-packaged commodity.

```
                           Structured Declarations & Bounding Boxes
                                              │
                                              ▼
                         ┌─────────────────────────────────────────┐
                         │   Versioned Statutory Rule Pack         │
                         │   lmpc_2026_09_v1.json (26 Rules)       │
                         └─────────────────────────────────────────┘
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
         Declaration Logic            Visual & Typography       Multi-Panel Consistency
         (Rules 6(1), 12, 13)         (Rule 7 Table-I, Rule 8)  (Rules 6(2A), 6(1)(da))
                    │                         │                         │
                    └─────────────────────────┼─────────────────────────┘
                                              ▼
                                26 Individual Check Findings
                                (PASS / FAIL / NOT_ASSESSED)
                                              │
                                              ▼
                               Package Overall Assessment Result
                         (COMPLIANT / VIOLATION / REVIEW_REQUIRED)
```

### 1.1 Non-Negotiable Statutory Safeguards
1. **Zero LLM Legal Rulings:** The LLM structures evidence text into typed JSON; deterministic code evaluates compliance. No language model output can ever declare a statutory violation.
2. **Anti-False-Violation Principle (Safe Outage Discipline):** Missing photographic evidence, optical ambiguity, or cloud OCR provider outage must produce `not_assessed`, never `fail`. A trader cannot be flagged for a violation because a back panel was not photographed or a cloud OCR service experienced a transient timeout.
3. **Anti-False-Compliance Principle (Honest Denominators):** A package is never marked `compliant` if any mandatory panel or declaration remains unassessed. The denominator is always transparently recorded (e.g. `14 of 19 checks assessed`).
4. **Physical Scale Mandate:** Numeral and letter height checks under Rule 7 Table-I strictly require an optical calibration standard (ID-1 card or 5 INR coin). If uncalibrated, the check produces `not_assessed` with a calibration capture task.

### 1.2 Arithmetic Counting Contract & Denominator Invariants
The engine strictly separates registered rule catalog entries from executable algorithmic checks:
- **26 Registered Statutory Rules:** Total rule catalogue defined in `lmpc_2026_09_v1.json`.
- **19 Executable Algorithmic Checks:** Checks defined by the active rule pack (`CHK01` through `CHK18`).
- **7 Manual-Review & Advisory Checks:** Rules addressing subjective labeling attributes, origin marking symbols under Rule 6(4A)(d), and statutory notice recommendations.
- **Scan Findings Returned (`total_findings`):** Itemized findings evaluated and returned for the specific scan. A full applicable assessment can return 19 findings, whereas a partially applicable assessment may return fewer based on commodity scope.

Across every scan assessment, the arithmetic relationships are strictly enforced:
$$\text{total\_findings} = \text{pass\_count} + \text{fail\_count} + \text{not\_assessed\_count}$$
$$\text{checks\_assessed} = \text{pass\_count} + \text{fail\_count}$$
$$\text{not\_assessed\_count} = \text{total\_findings} - \text{checks\_assessed}$$

**Distinct Check Identities & Deduplication:** Distinct check codes evaluate independent statutory requirements and are never collapsed. For example, `CHK06` (Principal Display Panel character height under Rule 7 Table-I) and `CHK06b` (Net Quantity declaration specific numeral height under Rule 7) remain distinct finding rows. Multi-panel deduplication strictly enforces that a definitive `fail` outranks a `pass`, and `pass` outranks `not_assessed` on identical check identities.

A package achieves `COMPLIANT` if and only if all applicable returned checks pass with zero unassessed or failed checks ($\text{checks\_assessed} = \text{total\_findings}$, with $\text{fail\_count} = 0$ and $\text{not\_assessed\_count} = 0$). In a full applicable assessment, $\text{pass\_count} = 19$.

---

## 2. Rule-Pack Versioning & Effective-Date Resolution

The legal engine loads versioned rule packs dynamically based on the inspection's statutory effective date (`rules_as_at`):

```python
# Date-aware statutory resolution in Backend/rules/loader.py
FOURTH_AMENDMENT_EFFECTIVE_DATE = date(2026, 9, 21)

if as_at_date >= FOURTH_AMENDMENT_EFFECTIVE_DATE:
    path = "lmpc_2026_09_v1.json"  # 26 Rules: Fourth Amendment baseline
else:
    path = "lmpc_2026_v1.json"     # 24 Rules: Pre-Fourth Amendment historical baseline
```

### Statutory Historical Baselines:
- **Fourth Amendment Baseline (`2026-09-21` onwards):** Incorporates G.S.R. 826(E). Inserts Rule 6(4A)(d) origin-marking requirements for soaps, shampoos, toothpastes, cosmetics, and toiletries. Omits former Rule 6(8).
- **Substituted Table-I Baseline (`2018-01-01` onwards):** Incorporates G.S.R. 629(E). All packages evaluate numeral height against **Table-I** (Table-II was permanently omitted).
- **Jan Vishwas Enforcement Tiers (`2026-05-01` onwards):** Incorporates Act 8 of 2026. Recommends advisory rectification notices for first-time technical labeling infractions prior to formal compounding.

---

## 3. Statutory Finding & Assessment State Machines

### 3.1 Per-Check Finding States (`FindingResult`)

Every individual check resolves to exactly one of three states:

| Finding State | Statutory Meaning | Trigger Condition |
|---|---|---|
| `pass` | Statutory Requirement Satisfied | Declaration observed, legally formatted, supported by verified provenance, within calibrated thresholds. |
| `fail` | Confirmed Statutory Breach | Mandatory declaration absent despite complete panel coverage, deceptive stickers, or below-minimum font height. |
| `not_assessed` | Inability to Verify | Incomplete panel coverage, optical blur/glare, missing calibration standard, or ambiguous statutory text. |

### 3.2 Package-Level Scan Assessment States (`ScanVerdict`)

Individual check findings aggregate into one of four overall package results:

```
┌─────────────────┬────────────────────────────────────────────────────────────┐
│ Scan Result     │ Aggregation Condition                                     │
├─────────────────┼────────────────────────────────────────────────────────────┤
│ COMPLIANT       │ ALL applicable checks returned PASS. Zero NOT_ASSESSED.   │
│ VIOLATION       │ At least ONE applicable check returned FAIL.               │
│ REVIEW_REQUIRED │ ZERO failures, but one or more checks are NOT_ASSESSED,    │
│                 │ or cross-panel statutory conflict detected.                │
│ OUT_OF_SCOPE    │ Package is statutorily exempt (e.g. weight <= 10g).        │
└─────────────────┴────────────────────────────────────────────────────────────┘
```

---

## 4. Rule Categories & Evaluation Logic

### 4.1 Mandatory Declaration Rules (Rule 6(1))
- **`CHK01`:** Verifies presence of all core declarations: Manufacturer/Packer/Importer identity, Commodity Generic Name, Net Quantity, MRP, Month & Year of packing, and Consumer Care details.
- **`CHK04`:** Date format validation. Verifies Month and Year format (`MM/YYYY` or Month name and year). Flags expired shelf-life where expiry is declared.
- **`CHK11`:** Address completeness. Verifies that manufacturer address contains identifiable city and PIN code.
- **`CHK19`:** Consumer care validation. Checks for valid customer care email, postal address, and telephone number under Rule 6(1)(g).

### 4.2 Typography & Minimum Font Height (Rule 7 Table-I)
Rule 7 Table-I mandates minimum font height based on Principal Display Panel (PDP) area:

$$\text{PDP Area } (A) \longrightarrow \text{Minimum Height } (H_{\text{min}})$$

| PDP Area ($A$ in $\text{cm}^2$) | Normal Packaging ($H_{\text{min}}$ in mm) | Blown / Moulded / Perforated ($H_{\text{min}}$ in mm) |
|---|---|---|
| $A \leq 50$ | 1.0 mm | 1.5 mm |
| $50 < A \leq 100$ | 1.5 mm | 3.0 mm |
| $100 < A \leq 500$ | 2.5 mm | 4.0 mm |
| $500 < A \leq 2500$ | 4.0 mm | 6.0 mm |
| $A > 2500$ | 6.0 mm | 6.0 mm |

- **`CHK06`:** Verifies numeral height for Net Quantity and MRP declarations against Table-I thresholds.
- **`CHK07`:** Verifies letter height for accompanying units (e.g. "g", "ml", "kg") is not less than twice the numeral height where applicable.

### 4.3 Standard Packaging Sizes (Rule 5 read with Second Schedule)
- **`CHK10`:** Verifies standard commodity packaging sizes for prescribed goods (e.g., biscuits, baby foods, edible oils). Where packaging deviates from standard Second Schedule quantities, verifies that Unit Sale Price (USP) is prominently declared under Rule 6(1)(f).

### 4.4 Multi-Panel Consistency & Anti-Evasion Rules
- **`CHK12` (Unit Sale Price):** Verifies that retail packages carrying declared MRP also display Unit Sale Price in standard statutory units (Rs. per g/kg/ml/L).
- **`CHK13` (Sticker Regulations):** Flags unauthorized price stickers under Rule 6(1)(da). A sticker is permissible only if it reduces price for consumer benefit; obscuring original markings is a violation.
- **`CHK14` (Dual Pricing):** Prohibits declaring multiple MRPs on identical packages under Rule 6(2A).

### 4.5 Fourth Amendment Origin Marking (Rule 6(4A)(d))
- **`CHK23` (Cosmetics & Personal Care Origin):** Added under G.S.R. 826(E) effective 21.09.2026 for soaps, shampoos, toothpastes, cosmetics, and toiletries.
- **Statutory Review Boundary:** The introductory phrasing of parent Rule 6(4A) is permissive. To prevent false prosecutions under Section 36(1), the engine treats omitted symbols on these commodities as **`REVIEW_REQUIRED` (Manual Officer Review)** rather than an automated violation.

---

## 5. Complete 26-Rule Statutory Inventory (`lmpc_2026_09_v1.json`)

| Check Code | Statutory Citation | Description | Severity | Statutory Limb |
|---|---|---|---|---|
| **CHK01** | Rule 6(1) | Core mandatory declarations on retail package | Critical | Section 36(1) |
| **CHK02** | Rule 26(a) | Small packaging statutory exemption (<= 10g / 10ml) | Advisory | None |
| **CHK03** | Rule 3 | Retail scope boundary (excludes wholesale/export) | Advisory | None |
| **CHK04** | Rule 6(1)(e) & 2(m) | Month and year of packing / import / expiry | Major | Section 36(1) |
| **CHK05** | Rule 12 & Rule 13 | Net quantity standard metric units (g, kg, ml, L) | Critical | Section 36(2) |
| **CHK06** | Rule 7(2) read with Table-I | Character/numeral height based on PDP area (Table-I) | Critical | Section 36(1) |
| **CHK06b** | Rule 7 Net Quantity Height | Specific minimum numeral height for net quantity declaration | Major | Section 36(1) |
| **CHK07** | Rule 7(3) | Letter height of unit symbols accompanying numerals | Minor | Section 36(1) |
| **CHK08** | Rule 9(1) | Clarity, contrast, and conspicuous declaration layout | Minor | Section 36(1) |
| **CHK09** | Rule 8 | Declaration placement on Principal Display Panel | Minor | Section 36(1) |
| **CHK10** | Rule 5 Second Schedule | Standard packaging quantities and sizing limits | Major | Section 36(1) |
| **CHK11** | Rule 6(3), 6(4), 6(4A) | Manufacturer, packer, or importer complete address | Major | Section 36(1) |
| **CHK12** | Rule 6(1)(aa) | Mandatory Unit Sale Price (USP) declaration | Major | Section 36(1) |
| **CHK13** | Rule 6(1)(da) | Sticker price adjustments and overprinting rules | Major | Section 36(1) |
| **CHK14** | Rule 2(h) Proviso | Dual pricing prohibition across identical packages | Critical | Section 36(1) |
| **CHK15** | Rule 6(10) | E-commerce marketplace mandatory declarations | Major | Section 36(1) |
| **CHK16** | Rule 6(10A) | Digital listing origin and manufacturer disclosures | Major | Section 36(1) |
| **CHK17** | FSSAI Alignment | Food commodity packaging alignment provisions | Advisory | None |
| **CHK18** | Section 36 Enforcement | Section 36 penalty limb attribution (36(1) vs 36(2)) | Critical | Section 36 |
| **CHK19** | Rule 6(1)(g) | Consumer care telephone, email, and address info | Major | Section 36(1) |
| **CHK20** | Rule 6(1)(m) | Country of origin on imported packaged goods | Critical | Section 36(1) |
| **CHK21** | Rule 6(1)(b) Proviso | Quantity declaration exemptions for specified goods | Advisory | None |
| **CHK22** | Rule 8 | Principal display panel grouping and clear space | Minor | Section 36(1) |
| **CHK23** | Rule 6(8) (Pre-Sep 2026) | Historical declaration rules (omitted 21.09.2026) | Major | Section 36(1) |
| **CHK23** | Rule 6(4A)(d) (Fourth Amend) | Origin-marking symbol on cosmetics/personal care | Major | Section 36(1) |
