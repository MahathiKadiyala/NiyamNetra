# Deployment & Environment Configuration

**Document Code:** `DOC-11`  
**Target Environments:** Local Development, Render Web Service, Supabase PostgreSQL & Storage, Vercel SPA  
**Security Classification:** Redacted Configuration (Zero Embedded Secrets)  

---

## 1. Production Deployment Topology

NiyamNetra is deployed across high-availability cloud infrastructure designed for low latency and zero maintenance overhead:

```
                  ┌─────────────────────────────────────────────────────────┐
                  │                 Cloud Infrastructure                    │
                  │                                                         │
┌──────────────┐  │   ┌──────────────────────┐      ┌─────────────────────┐ │
│  Field App   │──┼──▶│   FastAPI Backend    │─────▶│ Supabase PostgreSQL │ │
│ (Android     │  │   │  (Render Web Service)│      │  (AWS Session Pool) │ │
│  Release APK)│  │   │                      │      └─────────────────────┘ │
└──────────────┘  │   │  - Python 3.11       │                 │            │
                  │   │  - 26 Statutory Rules│      ┌─────────────────────┐ │
┌──────────────┐  │   │  - Cloud OCR / LLM   │─────▶│  Supabase Storage   │ │
│  Web Portal  │──┼──▶│  - Audit Ledger      │      │  (Evidence Bucket)  │ │
│ (Vercel SPA) │  │   └──────────────────────┘      └─────────────────────┘ │
└──────────────┘  │              │                                          │
                  │              ▼                                          │
                  │   ┌──────────────────────┐                              │
                  │   │ Vision & LLM APIs    │                              │
                  │   │ (Google, Groq, OCR)  │                              │
                  │   └──────────────────────┘                              │
                  └─────────────────────────────────────────────────────────┘
```

| Component | Technology | Hosting Platform | URL / Endpoint |
|---|---|---|---|
| **Backend API** | FastAPI + SQLAlchemy 2.0 (Python 3.11 container runtime, Python 3.11+ supported, Python 3.11/3.13 verified locally) | Render (Web Service) | `https://niyamnetra-backend.onrender.com` |
| **Database** | PostgreSQL 15+ | Supabase (AWS Frankfurt/Mumbai) | Port `5432` (Session Pooler) |
| **Object Storage** | S3-Compatible Storage | Supabase Storage | Bucket: `NiyamNetra_Images` |
| **Web Portal** | React 18 + Vite 5 + Tailwind | Vercel (Edge CDN) | `https://niyamnetra-legal-metrology-rules.vercel.app` |
| **Mobile Client** | React Native (Expo SDK 54) | Standalone Android APK | `release/NiyamNetra-v1.0.4-release.apk` |

---

## 2. Local Development Quick-Start

Run the entire platform locally in three independent terminal sessions:

### 2.1 Backend API (Terminal 1)
```bash
# 1. Navigate to Backend directory
cd Backend

# 2. Create and activate virtual environment
python -m venv niyamnetra_venv
# Windows (PowerShell): .\niyamnetra_venv\Scripts\Activate.ps1
# Linux / macOS: source niyamnetra_venv/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configure local environment
cp .env.example .env
# Edit .env and supply your local or remote DATABASE_URL and JWT_SECRET

# 5. Execute database migrations
alembic upgrade head

# 6. Seed demonstration stores and officer accounts
python seed.py

# 7. Start FastAPI development server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
*Verification:* Open [http://localhost:8000/health](http://localhost:8000/health) — `status: ok` and `checks_registered: 26`.

### 2.2 Web Portal (Terminal 2)
```bash
# 1. Navigate to Frontend_Portal directory
cd Frontend_Portal

# 2. Install dependencies
npm install

# 3. Launch Vite development server
npm run dev
```
*Verification:* Open [http://localhost:5173](http://localhost:5173) in your browser.

### 2.3 Mobile App (Terminal 3)
```bash
# 1. Navigate to Frontend_App directory
cd Frontend_App

# 2. Install dependencies
npm install

# 3. Start Expo development server
npx expo start
```
*Verification:* Scan the terminal QR code using Expo Go on Android or iOS.

---

## 3. Production Deployment Instructions

### 3.1 Deploying the Backend to Render
1. Connect your GitHub repository to Render.
2. Create a new **Web Service**:
   - **Environment:** `Python 3`
   - **Build Command:** `pip install --upgrade pip && pip install -r requirements-render.txt && alembic upgrade head`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 2`
3. Configure Environment Variables in Render Dashboard (see Section 4).
4. Health Check Path: `/health`.

### 3.2 Deploying the Web Portal to Vercel
1. Import `Frontend_Portal` project in Vercel Dashboard.
2. **Framework Preset:** `Vite`.
3. **Build Command:** `npm run build`.
4. **Output Directory:** `dist`.
5. Set `VITE_API_BASE_URL = https://niyamnetra-backend.onrender.com`.

### 3.3 Compiling the Release Android APK
```bash
cd Frontend_App

# Compile production standalone APK using Expo Application Services (EAS)
eas build --platform android --profile release --local
```
Output artifact: `release/NiyamNetra-v1.0.4-release.apk`.

---

## 4. Environment Variables Reference

> **CRITICAL SECURITY NOTICE:**  
> Never commit real secrets, passwords, or private keys to source control.  
> The values below are documented as variable definitions with placeholder syntax.

### 4.1 Backend Environment Variables (`Backend/.env`)

```ini
# --- Core Environment & Operational Mode ---
ENV=production
DEBUG=false
PUBLIC_BASE_URL=https://niyamnetra-backend.onrender.com

# --- Database & Storage (Supabase) ---
# Use Session Pooler URI on AWS for serverless resiliency
DATABASE_URL=postgresql+psycopg://<db_user>:<db_password>@<db_host>:5432/<db_name>?sslmode=require
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=<set_on_server>
SUPABASE_BUCKET_IMAGES=NiyamNetra_Images

# --- Authentication & Session Hardening ---
# Cryptographically random string with minimum 32 characters
JWT_SECRET=<set_on_server_min_32_chars>
ACCESS_TOKEN_EXPIRE_MINUTES=720
REFRESH_TOKEN_EXPIRE_DAYS=30

# --- Statutory Rules Engine Configuration ---
RULE_PACK_VERSION=2026.09.v1
RULES_AS_AT=2026-09-21
ENGINE_VERSION=2.0.0

# --- Cloud OCR Provider Configuration & Credentials ---
# Mode: 'auto' (fallback chain), 'google_vision', 'ocr_space', 'azure'
OCR_PROVIDER=auto
OCR_PROVIDER_CHAIN=google_vision,ocr_space,azure
# 1. Google Cloud Vision (Primary)
GOOGLE_APPLICATION_CREDENTIALS=<path_to_service_account_json_on_server>
GOOGLE_VISION_API_KEY=<optional_api_key_on_server>
# 2. OCR.space (Secondary Fallback)
OCR_SPACE_API_KEY=<set_on_server>
# 3. Azure Computer Vision (Tertiary Fallback)
AZURE_VISION_KEY=<set_on_server>
AZURE_VISION_ENDPOINT=https://<resource-name>.cognitiveservices.azure.com/

# --- Cloud LLM Provider Credentials ---
# Primary high-speed structured extraction (Groq Llama-3.3-70B)
GROQ_API_KEY=<set_on_server>
GROQ_MODEL=llama-3.3-70b-versatile
# Fallback extraction provider (Google Gemini Flash)
GEMINI_API_KEY=<set_on_server>
GEMINI_MODEL=gemini-1.5-flash

# --- Security Headers & CORS ---
CORS_ORIGIN_REGEX=^https:\/\/.*\.vercel\.app$|^https:\/\/.*\.onrender\.com$
MAX_UPLOAD_MB=25
```

### 4.2 Web Portal Variables (`Frontend_Portal/.env`)

```ini
# Production API endpoint
VITE_API_BASE_URL=https://niyamnetra-backend.onrender.com
```

### 4.3 Mobile App Variables (`Frontend_App/.env`)

```ini
# Production API endpoint embedded into release APK bytecode
EXPO_PUBLIC_API_BASE_URL=https://niyamnetra-backend.onrender.com
```
