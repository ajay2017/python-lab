#!/bin/sh
# Railway only injects flat env vars (no secrets.toml file on disk), but the
# app calls st.secrets.get(...) directly in ~50 places across app.py and
# stock_analyzer/. When no secrets.toml exists anywhere, Streamlit's lazy
# loader raises StreamlitSecretNotFoundError on the FIRST access (not just a
# missing-key miss), crashing whichever page touches it. Materializing a real
# secrets.toml from the already-configured Railway env vars fixes every call
# site at once, keeping the Variables tab as the single source of truth.
set -e
mkdir -p .streamlit
cat > .streamlit/secrets.toml <<TOMLEOF
[supabase]
url = "${SUPABASE_URL}"
key = "${SUPABASE_KEY}"

[anthropic]
api_key = "${ANTHROPIC_API_KEY}"

[app]
password = "${APP_PASSWORD}"
readonly_password = "${APP_READONLY_PASSWORD}"

[fred]
api_key = "${FRED_API_KEY}"

FINNHUB_API_KEY = "${FINNHUB_API_KEY}"
FMP_API_KEY = "${FMP_API_KEY}"
TOMLEOF

exec streamlit run app.py --server.port "$PORT" --server.address 0.0.0.0 --server.headless true
