# Assets

Brand assets used by the Streamlit app.

## Required files

| File | Purpose | Notes |
|---|---|---|
| `drishta_logo.png` | App favicon + sidebar header logo | Square, transparent background recommended. App falls back to 👁 emoji if missing. |

The app references `assets/drishta_logo.png` via `_BRAND_LOGO_PATH` in `app.py`. If the file is missing, branding still renders with an emoji fallback — no crash.
