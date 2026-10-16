# OCR dependencies — NiyamNetra (cloud-first)

Cascade in `Backend/ocr/factory.py` / `Backend/ocr_engine.py`:
1. Google Cloud Vision (`GOOGLE_VISION_API_KEY`, primary, multi-language support: `en`, `hi`)
2. OCR.space (`OCR_SPACE_API_KEY`, secondary fallback)
3. Azure AI Vision (`AZURE_VISION_KEY` + `AZURE_VISION_ENDPOINT`, tertiary fallback)
4. Local Tesseract (`eng+hin`, optional local developer fallback via `pytesseract`)
5. None / Honest statutory `not_assessed` (zero false violations on technical failure)

Render free (512MB): install `requirements-render.txt` (httpx + opencv only, no paddle/tesseract/zbar). Set at least one cloud key in `.env` / dashboard or every scan is not_assessed. Docker path (`Backend/Dockerfile`): `apt-get install tesseract-ocr libzbar0` + `pytesseract pyzbar` for local OCR without cloud.

Authoritative Technical Reference: [04_IMAGE_OCR_LLM_PIPELINE.md](../documents/04_IMAGE_OCR_LLM_PIPELINE.md)
Operational Smoke Test: [smoke_ocr_provider.py](scripts/smoke_ocr_provider.py) (`python scripts/smoke_ocr_provider.py --live-provider-call`)

