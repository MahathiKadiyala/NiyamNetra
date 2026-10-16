# Historical Adversarial Analysis: 90 Loopholes with Counter-Strategies

> **Historical Document Notice (Archived Baseline)**  
> **Source Commit:** `00dbeed` (`Docs/12_SIH26034_NiyamNetra_loopholes.md`)  
> **Document Status:** Historical engineering analysis and threat model.  
> **Authoritative Current Documentation:** For verified runtime behavior, implemented defenses, active security controls, and statutory rule evaluation, consult [`08_SECURITY_AND_PRIVACY.md`](../08_SECURITY_AND_PRIVACY.md), [`02_SYSTEM_ARCHITECTURE.md`](../02_SYSTEM_ARCHITECTURE.md), and [`05_LEGAL_COMPLIANCE_ENGINE.md`](../05_LEGAL_COMPLIANCE_ENGINE.md).  
> **Interpretation Note:** Proposed mitigations, experimental strategies, and rejected solutions in this document represent the historical adversarial exploration conducted during project design. They are preserved for auditability and engineering context and should not be assumed to be active unless documented in the canonical `documents/` suite.

---

# NiyamNetra - 90 Loopholes with Best-Optimised Solutions
### Smart India Hackathon 2026 - Problem Statement SIH26034 - Adversarial Analysis
**Version:** 7.0 | **Date:** 29 Aug 2026 | **Revised:** 30 Aug 2026
**Path:** C:\Skills\Projects\NiyamNetra\Docs\12_SIH26034_NiyamNetra_loopholes.md
**Scope:** Ninety distinct ways the system can be defeated or can fail, each with a solution implementable inside the offline, zero-paid-API stack

---

## 0. HOW TO USE THIS DOCUMENT

### 0.1 The constraint every solution here respects

`04_NiyamNetra_PRD.md` §1 and §6 and `07_Tech_Stack.md` §11 commit the project to an **offline core with zero paid APIs**. A loophole "solved" by calling a hosted vision model is not solved - it is deferred to a service that will not be reachable in the rural shop where the inspection happens, and that will bill per call once the free tier ends.

Version 6 of this document broke that constraint in nineteen places. Every one has been replaced. The rejected approaches and the reasons are recorded in §10 so that a code-generation tool reading this file does not reintroduce them.

### 0.2 Three classes of risk, not one

| Class | Meaning | Count |
|---|---|---|
| **Attack** | Someone deliberately defeats the system | 41 |
| **Failure** | The system produces a wrong answer without anyone trying | 32 |
| **Inverse risk** | The system's own defence harms an honest user | 17 |

The third class is the one every adversarial analysis forgets, and the one that decides whether inspectors keep using the tool after week two. A geofence that locks out an inspector standing inside a concrete-walled shop, or a duplicate-photo check that accuses an honest officer of fraud, will get the app uninstalled faster than any fraud it prevents. Seventeen entries below exist purely to keep the defences from misfiring, and they are marked **[inverse]**.

### 0.3 Reading an entry

Each entry states the attack or failure, then the solution. Where a plausible fix is wrong, the entry says so explicitly under *Why the obvious fix fails* - those are the sentences worth reading twice, because they are where version 6 went wrong.

---

## 1. EVIDENCE INTEGRITY AT CAPTURE (1-12)

### 1. Wrong product, correct image
**Attack:** The seller presents a photograph of a compliant pack - from the internet, or from a different shelf - while selling something else. The system audits the photograph, not the product.
**Solution:** Live camera only for the `inspector` role; the gallery picker is not merely discouraged but absent from the build for that role. At capture the app binds GPS, server-issued capture token, device install ID and store ID into the record, and computes SHA256 over the image bytes. A barcode read from the same frame is cross-checked against the selected commodity. The binding is what matters: content fingerprint alone proves the image is unaltered, not that it came from this shelf.

### 2. Edited or AI-generated image
**Attack:** A failing pack's photograph is retouched, or a compliant label is generated, to obtain a pass.
**Why the obvious fix fails:** Error Level Analysis is not a reliable forensic test - any re-saved JPEG shows the same block artefacts, so ELA generates false accusations against honest photographs. Rejecting images whose EXIF carries a software tag is worse: Android and iOS camera pipelines, and every server-side resize, write software tags routinely.
**Solution:** Do not attempt to detect forgery from the pixels. Remove the opportunity instead. The server issues a short-lived **capture token** when the inspector opens the camera for a specific store and commodity; the app must return an image whose SHA256 was computed on-device and submitted with that token inside its validity window, together with the frame's sensor metadata. An image produced anywhere other than in that session has no valid token. EXIF is retained as corroboration and recorded in the audit trail, never used as a sole ground for rejection.

### 3. Duplicate evidence reuse across shops
**Attack:** One compliant pack's photograph is submitted for ten shops.
**Solution:** Perceptual hash (`imagehash.phash`, 64-bit) on every upload, compared against prior hashes. Hamming distance below 5 is treated as the same image. The comparison must not be a full table scan - see §7.5 for the banded index. Corroborate with travel feasibility rather than raw distance: the same image at two stores requires an implied speed of `distance / elapsed_time`; above 80 km/h between two urban stores, or any non-zero distance within 60 seconds, is physically implausible and is flagged with the map extract attached.

### 4. Stale photograph
**Attack:** A photograph from six months ago, when the stock was compliant, is submitted for today's shelf.
**Solution:** The capture token in #2 already bounds this: the token is valid for the length of one capture session. As corroboration, EXIF `DateTimeOriginal` is compared with the server's clock and a divergence beyond five minutes is recorded as an anomaly on the record - see #6 before making it a hard block.

### 5. Gallery upload bypass
**Attack:** The inspector sideloads an older build, or drives the API directly, to submit a file that never came from the camera.
**Solution:** The control lives on the server, not in the UI. `POST /api/scans` requires a valid unused capture token; an upload without one is rejected with `422` regardless of which client sent it. Client-side restrictions are a convenience for the honest user; the token is the actual control. This is the general principle for every item in this section - **any check that lives only in the app is not a control.**

### 6. Device clock skew rejects an honest photograph **[inverse]**
**Failure:** A field phone whose clock has drifted by twenty minutes has every capture rejected as stale, and the inspector cannot work.
**Solution:** Never block on EXIF time alone. The server's own receipt time, bound to the capture token, is the authoritative timestamp; EXIF divergence is recorded as `clock_skew_seconds` on the record and surfaced to the admin as an anomaly, not to the inspector as a refusal. On login the app fetches server time and displays a one-tap "your device clock is 20 minutes behind" notice. Time correctness is the server's job; do not make it the inspector's problem.

### 7. GPS spoofing
**Attack:** The inspection is submitted from home with a mock location provider.
**Solution:** Geofence against the registered store coordinates at 100 m. Read Android's mock-location indicator (`Location.isFromMockProvider`) and record it on the scan. Require one photograph of the shop's name board, and OCR-match it against the registered store name. Two independent location proofs - satellite fix and a photograph of the premises - are considerably harder to fake together than either alone.

### 8. GPS drift indoors locks out an honest inspector **[inverse]**
**Failure:** Inside a concrete shop a fix can sit 300 m off, or never arrive. A hard geofence block means the inspection cannot be recorded at all.
**Solution:** Treat the geofence as **advisory with justification**, never as a hard block. Beyond the store radius the app requires a reason and the shop-board photograph, then submits with the canonical `geofence_status = "outside"` and the measured distance, accuracy and justification in `geofence_reason`. Where reported accuracy is too poor for the distance test to mean anything the status is `"unknown"` with the reason in `geofence_reason` (matching `routers/inspections.py`: `inside`/`outside`/`unknown` only — `outside_with_justification` and `unreliable_fix` are not valid values). The admin dashboard lists these for review. An inspector who cannot file a finding will stop filing findings.

### 9. Perceptual-hash false positive accuses an honest inspector **[inverse]**
**Failure:** Two genuinely different photographs of the same SKU on the same white shelf can land within Hamming distance 5. The honest inspector is told they reused evidence.
**Solution:** A pHash match is **never** presented as fraud. It opens a `duplicate_suspected` review item showing both images side by side, both GPS fixes and both timestamps, for a human to close. Escalate to a fraud flag only when the travel-feasibility test in #3 also fails. Additionally require a second signal before flagging - identical file size, or identical pHash **and** identical dHash - since agreement between two different hash families makes coincidence far less likely.

### 10. Metadata stripped or altered in transit
**Attack:** The client is modified to send altered GPS or timestamp fields.
**Solution:** Nothing the client asserts about time or place is trusted on its own. Server receipt time is authoritative. GPS is client-asserted by necessity, so it is stored with its accuracy radius, its mock-provider flag, and the shop-board photograph that corroborates it - and the report says "location as reported by the device", not "location verified".

### 11. Uploaded image replaced in storage afterwards
**Attack:** The stored evidence file is swapped after the fact.
**Solution:** SHA256 computed at upload and stored on the scan row. Every retrieval recomputes and compares; a mismatch blocks the view and raises an alert. The image directory is written once and never rewritten - the application has no code path that opens an evidence file for writing. A nightly job re-verifies every hash and reports drift. Filesystem immutability (`chattr +i`, or object lock in a hosted deployment) is a deployment hardening step, documented in `09_SECURITY.md`, not a dependency of the design.

### 12. Adverse finding with no evidence
**Failure:** A violation is recorded with no photograph, so it cannot support anything later.
**Solution:** Submission is blocked for any scan carrying a `fail` finding unless at least two images are attached - the full principal display panel, and a close-up of the specific defect. Both are hashed. Mirrors the evidence expectation behind the Seventh Schedule Form A and B record sheets.

---

## 2. IMAGE QUALITY AND OCR RELIABILITY (13-26)

### 13. Blur
**Failure:** Blur hides the net quantity or the date; OCR guesses and the guess passes.
**Why the obvious fix fails:** Super-resolution is not a remedy. A generative upscaler **invents** plausible glyphs - it will happily turn an illegible smear into a confident, wrong `Rs. 50`. Fabricated characters in evidence that supports a penalty is the single most dangerous thing this system could do, and version 6 recommended it twice.
**Solution:** Detect at capture and retake. Laplacian variance below 100 on the cropped panel triggers a haptic pulse and a red frame *before* upload. Burst five frames over 0.5 s and keep the sharpest. Permitted enhancements are strictly non-generative and are recorded in the audit trail: CLAHE, unsharp mask, adaptive threshold, deskew. No generative model touches evidence. Where the panel remains below threshold the outcome is `not_assessed`, reason `"image too blurred to read"` - never a guess.

### 14. Glare on foil and laminate
**Failure:** Specular reflection erases the MRP.
**Solution:** Detect saturated-pixel clusters above 15% of the panel area and prompt "tilt the pack away from the light; flash off" with flash disabled automatically. Bracket three exposures and select the panel with the largest readable-text area. Apply CLAHE. Unresolved glare over a required field yields `not_assessed`, not a missing-declaration violation - the field may well be there.

### 15. Low light
**Failure:** Dark shop, dark text, nothing legible.
**Solution:** Mean-luminance check below 50 prompts flash on and a move toward light. CLAHE plus gamma correction on the captured frame. Same fallback: `not_assessed`.

### 16. Shadow across the label
**Failure:** A hand or shelf edge shadows part of the panel and hides declarations.
**Solution:** Detect low-luminance regions with a hard edge covering more than 30% of the panel and force a retake with an on-screen arrow. Below 30%, normalise brightness locally and proceed, flagging the affected region on the finding.

### 17. Curved and crumpled packaging
**Failure:** Bottle curvature and pouch creases distort characters; height measurement is meaningless.
**Solution:** Guided three-shot capture - front, back, side - with an outline overlay. Perspective rectification by four-point homography on the detected panel corners, then cylindrical unwrapping for bottles using the diameter the inspector has already entered for the PDP calculation. PaddleOCR handles the rectified crop. Where curvature exceeds what unwrapping corrects, the *field-presence* checks still run on the readable text while the *metrology* checks return `not_assessed`. Presence and measurement degrade independently, and conflating them was a structural error in version 6.

### 18. Hidden or partially covered declarations
**Failure:** A fold, a finger or a shelf edge covers the MRP; the system reports it missing when it is merely obscured.
**Solution:** Completeness gate before any absence is asserted. If the panel's four corners are not all detected, or the detected panel area is materially smaller than the dimensions the inspector entered, the app returns `"incomplete capture - unfold the pack and retake"` and no missing-field finding is produced. **Absence may only be asserted from a frame proven complete.** This single rule removes most of the system's false-violation surface.

### 19. Cropped frame and multi-panel packs
**Failure:** MRP is on the back; only the front was photographed.
**Solution:** Required declarations may be spread across panels, so the flow is explicitly multi-panel: front, then back, then side where the pack has one. Findings aggregate across the set, and a field found on any panel counts as present. A single-panel capture cannot produce a missing-declaration finding at all - it produces `not_assessed`, reason `"only one panel captured"`.

### 20. White or clear print on transparent plastic
**Failure:** White text on a transparent bottle is invisible to the sensor.
**Solution:** Prompt the inspector to place any dark surface behind the bottle - a phone case, a notebook, the shelf back - and retake. Then grayscale inversion, CLAHE, adaptive threshold. This recovers the majority of cases at zero cost. Unrecovered cases are `not_assessed`, reason `"transparent substrate; text not separable"`.

### 21. Vertical and rotated text
**Failure:** Side-panel text set at 90 degrees is skipped by a horizontal-only pass.
**Solution:** Run detection at 0, 90, 180 and 270 degrees, keep the orientation with the highest mean confidence per region, and merge regions. PaddleOCR's detector handles arbitrary text angles natively; the four-way pass is a cheap safety net for near-vertical layouts.

### 22. Barcode and QR digits read as a price
**Failure:** `890103` under a barcode is parsed as an MRP.
**Solution:** Locate symbols with `pyzbar` first, mask their bounding boxes plus a small margin, then OCR. Decoded symbol payloads are stored in their own field. A price token whose bounding box overlaps a symbol region is discarded before any check runs.

### 23. Background text read as label text
**Failure:** A newspaper behind the pack contributes text; a required field appears to be present when it is not.
**Solution:** Segment the pack, crop to the panel, and OCR only inside it. Every extracted token carries its bounding box, and any token outside the panel polygon is dropped. Show the crop to the inspector for a one-tap confirmation before the checks run - this catches segmentation failures that no automated test would.

### 24. OCR hallucination
**Failure:** The engine emits confident text for a smeared region.
**Why the obvious fix fails:** Version 6 proposed cross-checking against a second hosted vision model. That reintroduces the paid, online dependency, and two models can agree on the same wrong reading.
**Solution:** Confidence discipline plus format validation, both local. A field below 0.60 confidence is `not_assessed` even where the block mean is higher. Every field must satisfy its format grammar - a price must carry a currency marker and a plausible magnitude; a quantity must carry a recognised unit. Grammar failure sends the field to review, never to a pass. Where a second opinion is genuinely wanted, run **Tesseract on the same crop** - it is already in the stack, runs offline, and disagreement between two independent local engines is a useful review trigger.

### 25. Character confusion - 0/O, 1/l, 5/S, 6/8
**Failure:** `Rs. 5O` and `5OOg` corrupt the extracted values.
**Solution:** Field-specific character whitelists at recognition time: numeric-only for the price magnitude and the quantity value. Then validate against the grammar. On failure, offer the inspector a correction with the crop shown beside the suggestion, log the correction, and never auto-accept a substitution silently. The correction log is also the project's cheapest source of real training data.

### 26. Decorative fonts and handwritten declarations
**Failure:** Stylised or handwritten text defeats recognition.
**Solution:** Recognition confidence drives the outcome; there is no separate stylised-font pathway. Detect handwriting by stroke-width variance and, where detected, mark the field `not_assessed` with reason `"handwritten declaration - requires manual reading"` and prompt the inspector to type what they see, storing their entry as operator-asserted rather than machine-read. The report distinguishes the two sources, because a hand-typed reading is a human's judgement and should be attributed as one.

---

## 3. MEASUREMENT AND THE SCALE PROBLEM (27-32)

Version 6 raised the millimetre problem four separate times (#9, #10, #37, #71) and answered it four slightly different ways, one of which cited a section number that does not exist. It is one problem. Here it is once, properly. Full treatment in `02_NiyamNetra_Rules.md` §7.4.

### 27. Millimetres cannot be read from pixels
**Failure:** Table-I is in millimetres. An image is in pixels. Without a scale, a height check is a guess, and a guess that drives a penalty tier is indefensible.
**Solution:** The inspector must enter the panel's physical dimensions anyway, because Table-I is banded by PDP **area** in cm² and the area cannot be computed without them. That entry supplies the scale: `mm_per_pixel = panel_height_mm / panel_pixel_height`. Optionally the inspector lays an ISO/IEC 7810 ID-1 card (85.60 × 53.98 mm - any debit card) or a Rs. 5 coin (23 mm) in frame; the pipeline derives a second scale and requires the two to agree within 5%. Disagreement yields `not_assessed`, reason `"scale sources disagree"`.

### 28. Zoomed capture makes sub-minimum text look compliant
**Failure:** A 3x close-up renders 0.8 mm text large and legible, and it passes.
**Solution:** Absolute measurement, from #27, is immune to this by construction - a scale derived from the pack's own dimensions is independent of camera distance. This is the reason to solve the scale problem properly rather than to warn the inspector about zoom.

### 29. Oblique capture understates character height
**Failure:** A tilted pack foreshortens the far edge; characters measure short and a compliant pack fails.
**Solution:** Rectify before measuring - four-point homography from the detected panel corners to a fronto-parallel view. Residual tilt above 15 degrees after rectification yields `not_assessed`, reason `"pack too oblique to measure"`.

### 30. Insufficient resolution for a millimetre claim
**Failure:** At 400 px across a 120 mm panel, one pixel is 0.3 mm; a 1.0 mm minimum is three pixels and the uncertainty swamps the tolerance.
**Solution:** Hard floor - panel pixel height below 400 px yields `not_assessed`, reason `"resolution insufficient for a millimetre measurement"`. Above the floor, report the measurement **with its uncertainty** and assert a violation only when the measurement's upper bound is still below the minimum: *"2.1 mm ± 0.3 mm against a 2.5 mm minimum"*. A finding that states its own error bars survives the objection that a camera is not a measuring instrument; one that does not, will not.

### 31. Band-boundary sensitivity **[inverse]**
**Failure:** A computed PDP area of 49 cm² sits in the ≤50 band at 1.0 mm; 51 cm² sits in the next at 1.5 mm. Measurement error near the boundary flips the verdict.
**Solution:** Where the computed area falls within 10% of a boundary, widen to the **more lenient** band and append `"near band boundary"` to the finding. Unproven margins resolve in favour of the person inspected, which is how a court would treat them.

### 32. Clear space is not measurable against artwork
**Failure:** Rule 8 requires clear space around the net quantity. Deciding what counts as intruding printed matter over a photographic background is not achievable at MVP reliability.
**Solution:** Attempt it only where the surround is separable by connected-component analysis. Over dense artwork, return `not_assessed`, reason `"surrounding region not separable from printed matter"`. This will be the system's most frequent `not_assessed`, and that is the correct outcome rather than a defect to be engineered away. Say so in the pitch before a judge asks.

---

## 4. LABEL EDGE CASES (33-46)

### 33. Sticker over the MRP
**Attack:** A sticker conceals the printed price or the importer's address.
**Solution:** Detect the sticker's edge discontinuity, texture break and white-point difference over the price region. A sticker that **raises** the price, or that occludes the printed original, is a violation; a price-reduction sticker leaving the original legible is compliant. Require a second photograph with the sticker lifted where the original is not readable.

### 34. Dual MRP versus the legitimate sticker case **[inverse]**
**Failure:** A struck-through old price beside a revised one is the lawful reduction case, not a dual-MRP breach - but a naive "two prices found" rule flags it.
**Solution:** Before asserting dual MRP, exclude the strikethrough pattern: a horizontal connected component crossing the price token's vertical centre by more than 60% of the token's width. Only two *live* prices constitute the breach.

### 35. Multiple prices across panels
**Failure:** Outer carton, inner sachet and a sticker carry three different prices; the engine picks one and passes it.
**Solution:** Collect every price token across all captured panels with its panel and bounding box. Never auto-select. Present all of them with locations and let the officer identify the original. The finding records all values.

### 36. Zone pricing across states **[inverse]**
**Failure:** The same product at Rs. 50 in one state and Rs. 55 in another is flagged as dual MRP.
**Solution:** Dual MRP concerns two prices on **identical commodities in the same market at the same time**. Different packs sold in different states are not the same package. Only prices appearing together on one pack, or on identical packs on one shelf, are compared. Cross-store price differences are never a dual-MRP finding.

### 37. Different variants read as dual MRP **[inverse]**
**Failure:** A 500 ml at Rs. 50 and a 1 L at Rs. 90 of one brand are flagged as dual MRP.
**Solution:** The comparison key is commodity **plus net quantity**. Differing quantity means differing commodity for this purpose, and no finding is produced.

### 38. Date sticker over the original date
**Attack:** A fresh best-before sticker covers an expired one.
**Solution:** Where two date tokens carry the same label type, or a date token sits within a detected sticker region, flag `"multiple dates - possible relabelling"` and require a lifted-sticker photograph. Report both dates.

### 39. "Free extra" not reflected in the net quantity
**Failure:** `500 g + 100 g free` with the net quantity still declared as 500 g.
**Solution:** Detect a promotional-quantity token near the net quantity. The declared net quantity must be the total the consumer receives. Where the promotional total exceeds the declared net quantity, flag it - and phrase the finding as *"declared net quantity 500 g appears not to include the 100 g stated as free"*, because whether the pack is short-declared or the promotion is puffery is the officer's call, not the engine's.

### 40. Combo and multipack inner units
**Failure:** The outer carton carries every declaration; the individual soap inside carries none.
**Solution:** Where the inspector marks the item a multipack, the flow requires both an outer and an inner capture, linked on one scan record. Inner units intended for individual sale carry their own declaration duty. The finding names which level failed, since the two attract different remedies.

### 41. Export-only pack sold in India
**Attack:** A pack marked "For export only" is sold domestically without Indian declarations.
**Solution:** Detect "for export only" or "not for sale in India". Present in India, this is a Rule 25 finding and the pack must be relabelled before domestic sale.

### 42. Loose goods repacked in branded film
**Attack:** Loose rice is packed in branded-looking film with no packer identification, so no one is answerable.
**Solution:** A pack carrying only "Marketed by" with no manufacturer or packer name and address is a Rule 6(1)(a) and Rule 10 finding. Flag `"no manufacturer or packer identification; possible unregistered repacking"`. Note this is also where the *packed in the purchaser's presence* question in #50 must have been answered - loose grain weighed at the counter is not a pre-packaged commodity at all.

### 43. Batch substitution
**Attack:** Batch A on the shelf is compliant; batch B in the backroom is not. The inspector scans A.
**Solution:** Capture the batch or lot number and the manufacture month and year on every scan, and key compliance history by commodity **and batch**. Where a commodity holds conflicting verdicts across batches, the history view says so and prompts confirmation of which batch is on the shelf.

### 44. A barcode is not an identity
**Failure:** A copied barcode makes a counterfeit pack look verified.
**Solution:** The barcode is recorded as an attribute and never treated as proof of authenticity or of compliance. The report states `"barcode present; authenticity not verified"`. NiyamNetra assesses declarations, not provenance - see #87.

### 45. QR or barcode covering a required declaration
**Failure:** A symbol is placed over the price or the quantity.
**Solution:** Compare the symbol's bounding box with the required-declaration boxes. Overlap that renders a declaration unreadable is the finding; the engine reports the measured overlap fraction and the affected field rather than asserting a statutory percentage threshold, since none exists.

### 46. Brand name accepted as the generic commodity name **[inverse]**
**Failure:** The largest text on the pack is the brand. Treating it as the commodity name produces a false pass on Rule 6(1)(b), and a pack bearing only a brand is in breach.
**Solution:** Never infer the commodity name from text size. Require it from a controlled commodity vocabulary, and reject a candidate that matches the detected brand token. Where no generic name is found, the finding is `"brand name present but no generic commodity name declared"`. This is the most common false pass in label-scanning systems and it is worth an explicit test case.

---

## 5. LEGAL SCOPE AND FALSE POSITIVES (47-58)

Every entry in this section prevents the system asserting a violation the law does not support. Collectively they matter more than the fraud controls: a tool that cries wolf is abandoned, and an abandoned tool prevents nothing.

### 47. Small package exemption **[inverse]**
**Failure:** A 5 g sachet is flagged for missing declarations although it is exempt.
**Solution:** Extract the net quantity first, then apply Rule 26(a) - 10 g or 10 ml or less is exempt, tobacco excluded. Display a green `"Exempt - small package"` state, not a red failure. The 10-20 g proviso was withdrawn with effect from 01.07.2012 and must not be applied. Where the net quantity cannot be read, the exemption is `not_assessed` and the scan carries a banner rather than silently proceeding as though the package were in scope.

### 48. Packages above 25 kg or for industrial and institutional consumers **[inverse]**
**Failure:** A 30 kg industrial chemical drum is flagged as a retail violation.
**Solution:** Ask the transaction type at scan time - retail, institutional, industrial, or packed in the purchaser's presence - and read the declared quantity. Outside Chapter II, the scan returns a single out-of-scope result with the reason, not eighteen passes. Reporting "compliant" for a package the Rules never touched is a false assurance and is as damaging as a false violation.

### 49. Cement and fertiliser to 50 kg **[inverse]**
**Failure:** A 50 kg cement bag is excluded as being over 25 kg, when the exception brings it back in scope.
**Solution:** Commodity-conditional threshold: cement, fertiliser and agricultural farm produce remain in scope up to 50 kg; everything else exits above 25 kg or 25 L.

### 50. Quantity determined in the purchaser's presence **[inverse]**
**Failure:** Grain weighed at the counter, or fabric cut to order, is assessed as a pre-packaged commodity. It is not one.
**Solution:** `"packed in my presence"` is a first-class option on the transaction-type question and exits Chapter II immediately. Easy to miss, and it is the single most common category of shop transaction in the country.

### 51. FSSAI requirements read as Legal Metrology requirements **[inverse]**
**Failure:** A missing FSSAI licence number is reported as an LM violation.
**Solution:** The FSSAI check is advisory and structurally incapable of failing - see `03` CHK17. Every report carries the scope disclaimer in `02` §16.1. Food labelling sits under the Food Safety and Standards Act 2006 and the 2020 labelling regulations, which this system does not assess and has no authority to assess.

### 52. Medical devices assessed under the wrong instrument **[inverse]**
**Failure:** A device pack is measured against Table-I when the 2025 proviso to Rule 2(h) routes it to the Medical Devices Rules 2017.
**Solution:** Classify from the operator's category selection plus label evidence, and route the metrology checks to `not_assessed` with the reason naming the correct instrument. The category list must actually contain "Medical Device" - `08_UI_DESIGN.md` omitted it, so the routing could never fire.

### 53. Non-SI units
**Failure:** `1 Dozen Pencils` instead of a count declaration.
**Solution:** Permit g, kg, ml, l, mm, cm, m and the count markers N and U. Flag dozen, score, gross and imperial units, and suggest the compliant form. Consult the Fourth Schedule for commodities declared in a unit other than the default, and return `not_assessed` for commodities absent from the transcribed table.

### 54. MRP without the tax-inclusive wording
**Failure:** `Rs. 50` alone, with no "inclusive of all taxes".
**Solution:** Require the tax phrase within the same declaration block as the price token, allowing the common abbreviations. Report the rounding convention separately and as a minor finding.

### 55. ISO-format dates flagged as violations **[inverse]**
**Failure:** Version 6 instructed the engine to flag `2025-08` as a violation because it is not `08/2025`. **That is wrong and would generate false violations at scale.** Rule 6(1)(d) requires the month and the year to be declared; it does not prescribe a single separator or field order. `2025-08`, `08/2025` and `AUG 2025` all convey August 2025 unambiguously.
**Solution:** Parse permissively - accept `MM/YYYY`, `MM-YYYY`, `YYYY-MM`, `MON YYYY`, `MONTH YYYY` and the common Indian variants. Flag only a date that is genuinely **ambiguous or absent**: `03/04` could be either month order and is a real defect. Where a full date is printed, the month and year are present and the requirement is met. Also honour the proviso exempting certain commodities from the date declaration.

### 56. Consumer care email treated as mandatory **[inverse]**
**Failure:** A missing email address is reported as a Rule 6(2) breach.
**Solution:** Rule 6(2) requires name, address and telephone number, and email **where available**. A missing email is not a finding. A missing telephone number is.

### 57. Rules applied as at the wrong date
**Failure:** Stock manufactured before 01.01.2018 is judged against the Table-I substituted on that date.
**Solution:** Store `rule_version` on every scan and show it in the report footer. Note the subtlety version 6 missed: the offence in Section 36 is committed by **selling** a non-conforming package, so the governing date is ordinarily the date of sale, not the date of manufacture. Manufacture date is relevant to what could have been complied with at packing time and is a mitigation the officer weighs - it is not an automatic exemption. The engine therefore applies the rules in force **on the scan date**, records the manufacture date, and where the two straddle an amendment adds `"pack predates the amendment in force at inspection"` for the officer to consider. Do not silently downgrade the applicable law from a printed date.

### 58. Citing a sub-rule the project has not verified
**Failure:** A notice cites "Rule 12(6)" when the provision is elsewhere, and the finding collapses.
**Solution:** Citations are data with a `verified` flag; unverified entries render as descriptive prose instead of a pinpoint reference, enforced by a CI test. See `02` §1.1 and §15.1. The prohibited-qualifier provision is precisely such a case - version 6 cited it as Rule 12(6) in one entry and Rule 13 in another.

---

## 6. HUMAN FACTORS (59-70)

### 59. Selective inspection - only compliant stock is presented
**Attack:** Non-compliant stock stays in the backroom; five good packs are offered.
**Solution:** The admin assigns the **category** for the day, not the inspector - "inspect biscuits today". Require a wide shelf photograph alongside the item scans, and compare the count of packs visible on the shelf against the count scanned, surfacing the coverage ratio on the report. Low coverage is not itself misconduct; it is a number the supervisor can see and ask about.

### 60. Compliant stock staged for inspection day
**Attack:** The shop keeps a small compliant set for inspections.
**Solution:** No schedule is exposed in the app. Rotate assignment so the same inspector does not cover the same area consecutively. Re-audit a sample of stores within seven days using a different inspector, and compare batches.

### 61. Front-facing stock only
**Attack:** Compliant packs at the front, older stock behind.
**Solution:** The app issues a randomised sampling instruction - "take the third pack from the back of the second row" - and requires a shelf-depth photograph. Randomised selection is the whole point; inspector choice is what the shop plans around.

### 62. Stock replaced after the inspection
**Attack:** Compliant packs during the visit, cheaper non-compliant stock afterwards.
**Solution:** Record the batch and manufacture date at scan time, and re-audit a sample of stores within seven days via a different inspector. A different batch on re-audit is visible in the history.
**Why the obvious fix fails:** Version 6 proposed printing a QR receipt on a Bluetooth thermal printer and affixing it to the shelf. No inspector carries a thermal printer, affixing notices to a trader's shelf is not an incidental power, and the receipt would be removed the moment the officer left. Dropped.

### 63. Deliberate false pass
**Attack:** A failing pack is recorded as compliant.
**Solution:** The engine's verdict is recorded independently of the inspector's. Where the inspector overrides an engine `fail` to a pass, a written reason is mandatory and the override is written to the append-only audit log with both verdicts retained. Rate anomalies - an inspector whose pass rate sits far above the district distribution - surface on the admin dashboard. Note that overrides are **legitimate and necessary**: the engine is often wrong, and an inspector who cannot override it will stop using it. The control is that overrides are attributable and visible, not that they are hard.

### 64. Collusion at the supervisory level
**Attack:** The override is approved from above.
**Solution:** Overrides and approvals are hash-chained into the same append-only log as everything else, with the approver's identity. The chain makes retrospective editing detectable. Beyond that, this is an institutional problem, not a software one, and the honest position in the pitch is that the system's contribution is a tamper-evident record for an auditor - not a claim to have solved bribery.
**Why the obvious fix fails:** Version 6 answered this with a public consumer-facing QR channel for challenging verdicts. That is a second product with its own moderation, abuse and privacy burden, and it is outside the MVP. Recorded in §9 as a future direction.

### 65. Impersonation of an inspector
**Attack:** A non-officer conducts inspections.
**Solution:** Accounts are provisioned only by an admin - there is no self-registration. Authenticate with a password plus a one-time code to the registered number. Bind the account to a device using a server-issued **install identifier** generated on first launch and stored in the platform keystore; a login from a new install requires admin re-approval.
**Why the obvious fix fails:** Version 6 specified IMEI binding. Android has restricted `IMEI` to privileged system apps since Android 10, so this is not implementable at all. Government SSO integration is the right long-term answer and is out of hackathon scope; it belongs in §9.

### 66. Account sharing
**Attack:** One account is used by several people, so findings are unattributable.
**Solution:** One active install per account, from #65. A new install invalidates the previous session and writes an audit entry. Concurrent sessions from distant locations are surfaced as an anomaly.

### 67. Superficial inspection
**Attack:** Ten products are cleared in twenty seconds without reading anything.
**Why the obvious fix fails:** Version 6 mandated a 30-second dwell timer per product with the submit button disabled. This punishes the competent - a practised inspector reads a familiar label in eight seconds - and it is trivially defeated by putting the phone down. It converts the tool into an obstacle, and inspectors route around obstacles.
**Solution:** Make thoroughness cheap rather than making speed expensive. Each finding the engine raises requires an explicit acknowledgement - there is no select-all - so time spent scales with the number of findings rather than with a fixed timer. Record time-on-task as a **statistic**, not a gate: the dashboard shows median seconds per scan against the district distribution, and a sustained outlier is a supervisory conversation. Where the engine raised no findings at all, a single confirmation is entirely appropriate and should be fast.

### 68. The shop owner later denies the inspection
**Attack:** The visit is disputed after the fact.
**Solution:** Capture the owner's or representative's signature on the device canvas at submission, plus a photograph of the premises. Both are embedded in the report with the timestamp and the reported location. Where the owner declines to sign - which they may - record the refusal explicitly rather than blocking submission.

### 69. Two inspectors, one shelf
**Failure:** The same shelf is inspected twice, inflating both violation counts and coverage statistics.
**Solution:** Deduplicate on store, commodity, batch and day at ingest. The second record is retained but marked `duplicate_of`, and excluded from aggregate counts. Without this, every dashboard number is quietly wrong.

### 70. Store re-registered to reset the violation tier
**Attack:** The Section 36 tier escalates with prior instances, so the shop re-registers under a relative's name and returns to first-instance treatment.
**Solution:** Match stores on normalised address and geographic proximity as well as on name and registration number, and surface `"a prior store record exists within 50 m with a different proprietor name"` to the officer. The system must not merge them automatically - a genuine change of ownership is lawful and common - but it must not let the history vanish silently either. Then be honest in the report: the tier is computed from *instances recorded in this system*, which is what `03` CHK18 requires the wording to say.

---

## 7. SYSTEM SECURITY (71-82)

### 71. Record edited after submission
**Attack:** A failing record is amended to a pass two days later.
**Solution:** Append-only audit log with SHA256 chaining - each entry hashes the previous entry's hash together with its own payload. Amendments create a new version; nothing is overwritten and nothing is deleted. The history view shows the diff, the actor and the stated reason.

### 72. Direct database tampering
**Attack:** Someone with database credentials edits a row.
**Solution:** Database triggers reject `UPDATE` and `DELETE` on the audit table - and note in `06_DATABASE.md` that trigger syntax differs between SQLite and PostgreSQL, so both must be written and both must be tested. A daily job recomputes the chain and reports any break, writing the chain head hash to a file outside the database. A direct edit cannot produce a consistent chain, so it is detectable on the next verification pass even though it cannot be prevented at the storage layer.
**Why the obvious fix fails:** Version 6 cited Supabase Row Level Security. Supabase is not in this project's stack, which uses SQLAlchemy against SQLite or PostgreSQL. Authorisation is enforced in the API layer - see #78.

### 73. Offline edit fraud
**Attack:** A record is altered while the device is offline, then synced as though it had always been that way.
**Solution:** Offline records carry their local creation time, their sync time and an `edited_offline` flag with the full local revision history. The server preserves both timestamps and refuses to collapse them. Offline edits appear in a dedicated admin review queue and are never auto-approved.

### 74. Replay of a captured request
**Attack:** A previously successful submission is resent.
**Solution:** Every mutating request carries a UUID nonce and a timestamp; the server stores nonces for 24 hours and rejects a repeat. The capture token in #2 is single-use, which independently defeats scan replay.

### 75. Sync queue poisoning
**Attack:** The offline queue is manipulated on a rooted device to inject records for stores never visited, or to submit hundreds at once.
**Solution:** Every queued item is validated server-side on arrival exactly as a live submission would be - capture token, geofence status, store assignment - and a batch is not trusted because it arrived as a batch. Rate-limit per account per day at a level well above honest use. Assignment matters most: a record for a store never assigned to that inspector is rejected outright.

### 76. Server-side request forgery via the listing URL
**Attack:** The e-commerce check fetches an operator-supplied URL. `http://169.254.169.254/`, `http://localhost:8000/api/admin` or `file:///etc/passwd` turns the backend into a proxy into its own network.
**Solution:** This was absent from version 6 entirely and is the most serious unaddressed vulnerability in the design. Allow only `http` and `https`; resolve the hostname and reject any address in a private, loopback, link-local or reserved range - re-checking **after** resolution to defeat DNS rebinding; disable redirect following, or re-validate every hop; cap response size and timeout; and run the fetch from a component with no credentials for internal services. Ideally restrict to an allowlist of the major marketplace domains, since those are the only listings the check is meant to assess.

### 77. Token theft via cross-site scripting
**Attack:** Injected script reads the JWT from `localStorage` and exfiltrates it.
**Solution:** React escapes by default, so the exposure is `dangerouslySetInnerHTML` and any direct DOM write - neither of which should appear in this codebase, and a lint rule should enforce that. OCR-extracted text is untrusted input and must never be rendered as HTML. Serve a strict Content-Security-Policy. Prefer an `HttpOnly`, `Secure`, `SameSite=Strict` cookie over `localStorage` for the portal; the Expo app keeps its token in the platform keystore, which is not reachable from web content.

### 78. Broken access control and insecure direct object references
**Attack:** An inspector requests `/api/inspections/842`, another inspector's record, and reads or amends it.
**Solution:** Authorise on every request in the API layer, from the role and identity in the verified token, and scope every query by owner - `WHERE inspector_id = current_user.id` for inspectors, unrestricted for admins. Never derive authorisation from a client-supplied identifier, and never rely on the UI hiding an action. Cover this with a test that asserts a 403 for cross-user access on every endpoint, since this class of bug does not show up in ordinary use.

### 79. Report tampering after generation
**Attack:** A PDF is edited locally and presented as issued.
**Why the obvious fix fails:** Version 6 said "PyJWT signs the PDF". PyJWT signs JSON Web Tokens; it cannot produce a PDF signature. It also promised verification at a `.gov.in` address that this project does not control.
**Solution:** Compute SHA256 over the generated PDF, store it against the inspection, and embed a QR code containing the inspection identifier and that digest. Verification is a page on the project's own portal that recomputes and compares. Where a true PAdES signature is wanted later, `pyhanko` does it properly with a real certificate - but the hash-and-QR approach is honest, needs no certificate authority, and can be demonstrated end to end.

### 80. Oversized or malicious upload
**Attack:** A 200 MB file, or a decompression bomb, exhausts memory or disk.
**Solution:** Enforce a request size limit at the reverse proxy and in FastAPI. Validate the content type and re-encode every image through Pillow before storing, which discards embedded payloads. Cap pixel dimensions and set `Image.MAX_IMAGE_PIXELS`. Reject anything that fails to decode.

### 81. Secrets in the repository
**Attack:** A committed `.env` exposes the signing key; forged tokens follow.
**Solution:** `.env` is git-ignored; only `.env.example` with placeholder values is committed. Generate the signing key with `secrets.token_urlsafe(64)` per environment. The application refuses to start if the key is missing, shorter than 32 bytes, or equal to any example value - a startup assertion, so the failure is loud and immediate. Note that `14_env_example.md` currently ships a weak literal default, which is exactly the pattern this prevents.

### 82. Data loss
**Failure:** Records are lost and there is nothing to rely on later.
**Solution:** For SQLite, `VACUUM INTO` produces a consistent snapshot without stopping the service; schedule it, plus a CSV export, to a second local disk and to whatever off-site copy the deployment allows. For PostgreSQL, `pg_dump` plus write-ahead-log archiving for point-in-time recovery. Verify by **restoring** on a schedule - an unrestored backup is a hypothesis. Retention is set to five years, subject to #88.

---

## 8. E-COMMERCE AND PLATFORM (83-86)

### 83. Listing omits required declarations
**Attack:** The product page shows a price and an image and nothing else.
**Solution:** Parse the listing and check every Rule 6(1) declaration **except** the month and year of manufacture, which Rule 6(10) expressly excludes - flagging its absence would be a false violation. Retain the rendered page as evidence. Fetching is subject to #76 without exception.

### 84. Platform lacks the country-of-origin filter
**Attack:** No searchable origin filter, contrary to Rule 6(10A), in force 01.07.2026.
**Solution:** Assess once per platform per day and cache the result. Emitting this finding on every product scan would inflate the violation count by the size of the catalogue. Where the scan date precedes 01.07.2026, the check returns `not_assessed` - correct for back-dated records and it documents the commencement date in executable form.

### 85. Attribution between seller and platform
**Failure:** The marketplace is named as the violator for a seller's omission, or the reverse.
**Solution:** Record both the seller and the platform on the finding, and attribute the declaration duty to the seller while noting the platform's role. Intermediary liability turns on the safe-harbour conditions under the Information Technology Act 2000 and on the marketplace's own conduct - a legal assessment the officer makes, not one the engine asserts.

### 86. Listing image differs from the delivered product
**Attack:** The listing shows a compliant pack; a different variant is delivered.
**Solution:** Where both a listing capture and a delivered-pack photograph exist, compare perceptual hashes and compare the extracted fields. Report the field-level differences - a price or quantity mismatch is the substantive finding, while an image difference alone may be nothing more than new packaging photography.

---

## 9. OPERATIONAL AND LAWFUL-USE RISKS (87-90)

The four risks in this section are the ones most likely to be raised by a judge who works in enforcement, and the least likely to appear in a hackathon submission.

### 87. The report is mistaken for a statutory notice
**Risk:** A generated PDF is served on a trader as though it were an improvement notice or a prosecution document. It is not one, and using it as one would be an abuse of process that discredits the project.
**Solution:** The disclaimer in `02` §16.1 is printed on every report, stating that findings on character height, clear space and contrast are photogrammetric estimates with stated uncertainty rather than measurements by a verified instrument, that net quantity has not been verified by weighing, that unverified citations are marked as such, and that the document is an inspection aid and not a statutory notice. The report is laid out to mirror the Seventh Schedule Form A field order so an officer can transcribe from it into the statutory form - transcribe, not substitute.

### 88. Bystander images, and retention against data minimisation
**Risk:** Shelf and shop-front photographs capture staff and customers. The Digital Personal Data Protection Act 2023 requires a lawful purpose and data minimisation, which sits in tension with a five-year retention rule.
**Solution:** Capture guidance directs the frame to the shelf and the pack rather than to people. Detect faces on upload and blur them by default in any image rendered into a report, retaining the original as evidence under access control. Record the purpose of processing, restrict who may view evidence images, log every access - the audit log already supports this - and set retention by record type rather than uniformly. Say plainly in the submission that this is a live tension being managed, not a solved problem; a considered answer here is far stronger than silence.

### 89. Device constraints in the field
**Failure:** Continuous camera preview with on-device processing drains a low-end phone in under two hours and throttles on heat. Days offline fill local storage and sync fails.
**Solution:** Run quality detection on sampled frames rather than every frame, and stop the preview whenever the app is not actively capturing. Do the OCR server-side where a network is available and only fall back to on-device processing when it is not. Cap the offline store, warn at 80% of capacity, and upload full-resolution evidence lazily - queue a compressed copy first so the record syncs even on a poor connection, then backfill the original. Show the queue depth and the remaining capacity on the home screen, because an inspector who does not know the queue is full will lose work.

### 90. Accessibility and the language of the report **[inverse]**
**Failure:** A pass-fail interface distinguished only by red and green is unreadable to roughly one man in twelve with a colour vision deficiency. A report in English alone cannot be read by many of the traders it concerns.
**Solution:** Never encode a verdict in colour alone - pair every state with an icon and a word, so `Pass`, `Violation` and `Not assessed` are distinguishable in greyscale. Meet WCAG 2.1 AA contrast for text; `08_UI_DESIGN.md` claims this and three of its semantic colours do not, with the measured ratios and corrected values in that file. Ensure a 44 px minimum touch target for one-handed use. Generate the report with the finding text in English and Hindi at minimum, structured so further languages are a data addition rather than a code change - the same discipline as the citation catalogue in `02` §1.1.

---

## 10. SOLUTIONS DELIBERATELY REJECTED

Recorded so that a code-generation tool reading this file does not reintroduce them. Each was present in version 6.

| Rejected | Why | Replaced by |
|---|---|---|
| Hosted vision model as primary or fallback OCR (nineteen entries) | Paid, online; breaks the offline mandate in `04` §1 and §6 and `07` §11. The inspection happens where there is no network. | PaddleOCR primary, Tesseract as the local second opinion, confidence discipline - #24 |
| Generative super-resolution on evidence | **Invents glyphs.** Fabricated characters supporting a penalty finding are indefensible. | Non-generative enhancement only; `not_assessed` below threshold - #13 |
| Error Level Analysis for forgery detection | Not a reliable forensic test; any re-saved JPEG shows the same artefacts, producing false accusations. | Server-issued single-use capture token - #2 |
| Rejecting images with a software tag in EXIF | Camera pipelines and any server resize write software tags. Would reject honest photographs. | EXIF as corroboration only, never a sole ground - #2 |
| Supabase Storage, Supabase Row Level Security | Not in the project's stack, which is SQLAlchemy against SQLite or PostgreSQL. | API-layer authorisation and owner-scoped queries - #78 |
| Amazon S3, S3 Object Lock, S3 Glacier | Paid and online; contradicts the local-filesystem evidence store. | Write-once local storage with hash verification - #11, #82 |
| IMEI-based device binding | Restricted to privileged system applications since Android 10. Not implementable. | Server-issued install identifier in the platform keystore - #65 |
| Bluetooth thermal printer receipt affixed to the shelf | No inspector carries one; affixing notices to a trader's shelf is not an incidental power; it would be removed immediately. | Batch capture plus randomised re-audit - #62 |
| Public consumer QR challenge channel | A second product, with its own moderation, abuse and privacy surface. Outside the MVP. | Recorded as a future direction; hash-chained overrides for now - #64 |
| Forced 30-second dwell timer per product | Punishes competent inspectors, trivially defeated by setting the phone down, converts the tool into an obstacle. | Per-finding acknowledgement; time-on-task as a statistic - #67 |
| "PyJWT signs the PDF" | PyJWT signs JSON Web Tokens. It cannot sign a PDF. | SHA256 digest with a QR verification link; `pyhanko` if a true PAdES signature is later required - #79 |
| Verification hosted at a `.gov.in` address | The project does not control that domain. | Verification page on the project's own portal - #79 |
| Flagging `2025-08` as a date-format violation | Legally wrong. Rule 6(1)(d) requires month and year, not one separator. Would produce false violations at scale. | Permissive parsing; flag only ambiguity or absence - #55 |
| Asserting a numeric contrast threshold as statutory | Rule 9 requires conspicuous contrast and specifies no ratio. | Measured ratio reported against a labelled internal advisory threshold - `03` CHK08 |
| Invented accuracy figures ("45% to 88%", "95% accuracy", "70% confidence") | Unmeasured numbers presented as results. A judge who asks how they were obtained gets no answer. | Measure against the labelled test set in `10_TESTING.md` and report what is measured |

### 10.1 Future directions, honestly labelled

Not in the MVP, and not claimed to be: government single-sign-on integration, biometric inspector authentication, Bluetooth weighing-scale integration for net quantity verification under the First and Sixth Schedules, a public consumer reporting channel, and a brand-registry cross-check for counterfeit detection. Each is a defensible next step. Presenting any of them as built would be the fastest way to lose a technical panel's confidence.

---

**End of 90 Loopholes.** Ninety distinct risks across attack, failure and inverse-risk classes. Every solution is implementable with the stack in 07_Tech_Stack.md, offline, with no paid API. Rejected approaches are in §10 - do not reintroduce them.
