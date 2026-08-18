#!/bin/bash
# Strict mode: abort on any error
set -e

RESOURCES_DIR="./resources/wheels"
mkdir -p "$RESOURCES_DIR"

echo "🔒 Starting secure resource vendoring..."

# Function to download and cryptographically verify a wheel
download_and_verify() {
    local url=$1
    local filename=$2
    local expected_hash=$3
    local filepath="$RESOURCES_DIR/$filename"

    echo "----------------------------------------"
    echo "⬇️ Fetching: $filename"

    # Download silently but show errors
    curl -sSL "$url" -o "$filepath"

    echo "🛡️ Verifying SHA-256 hash..."
    # Calculate the SHA-256 hash (compatible with standard Linux coreutils)
    local actual_hash=$(sha256sum "$filepath" | awk '{print $1}')

    if [ "$actual_hash" != "$expected_hash" ]; then
        echo "❌ CRITICAL SECURITY FAILURE: Hash mismatch for $filename!"
        echo "   Expected: $expected_hash"
        echo "   Actual:   $actual_hash"
        echo "🗑️ Deleting compromised file..."
        rm -f "$filepath"
        exit 1
    else
        echo "✅ Hash verified successfully. File is safe."
    fi
}

# Load environment configuration
if [ -f .env ]; then
    . .env
else
    echo "Warning: .env file not found, using .env.example"
    . .env.example
fi

# 1. Spacy en_core_web_sm Model
SPACY_URL="${SPACY_WHEEL_URL:-https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl}"
SPACY_FILE="en_core_web_sm-3.8.0-py3-none-any.whl"
SPACY_EXPECTED_HASH="${SPACY_WHEEL_HASH:-1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85}"

download_and_verify "$SPACY_URL" "$SPACY_FILE" "$SPACY_EXPECTED_HASH"

# 2. Frontend Assets
FRONTEND_DIR="./static/vendor"
mkdir -p "$FRONTEND_DIR"

echo "----------------------------------------"
echo "🌐 Downloading Frontend Assets..."

# HTMX
HTMX_URL="https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js"
HTMX_FILE="htmx.min.js"
HTMX_HASH="b3bdcf5c741897a53648b1207fff0469a0d61901429ba1f6e88f98ebd84e669e"

# Swagger UI JS
SWAGGER_JS_URL="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.9.0/swagger-ui-bundle.js"
SWAGGER_JS_FILE="swagger-ui-bundle.js"
SWAGGER_JS_HASH="2a556306524bed2ca668ec5ae19b1dbd4d9cdaa34795c9063a1c44b29a9c6097"

# Swagger UI CSS
SWAGGER_CSS_URL="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.9.0/swagger-ui.css"
SWAGGER_CSS_FILE="swagger-ui.css"
SWAGGER_CSS_HASH="c24ecffd63fc797d37bed1c68ea030479ad1c7a30638ffb6b5a2559ea98bc431"

# Mermaid JS (Standalone UMD bundle)
MERMAID_JS_URL="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"
MERMAID_JS_FILE="mermaid.min.js"
MERMAID_JS_HASH="8d607d7ef1d077a8aa202e18e62212bfa992c68bfeabc5cf45d51a128fe6675d"

# Datastar JS (Reactive SSE client)
DATASTAR_JS_URL="https://cdn.jsdelivr.net/gh/starfederation/datastar@1.0.0-beta.9/bundles/datastar.js"
DATASTAR_JS_FILE="datastar.js"
DATASTAR_JS_HASH="11d9e34fecd2ca69b9faf9096bbd33feea2c79a732372337f34950a617538768"

# We override RESOURCES_DIR temporarily for the download_and_verify function
ORIGINAL_RESOURCES_DIR="$RESOURCES_DIR"
RESOURCES_DIR="$FRONTEND_DIR"

download_and_verify "$HTMX_URL" "$HTMX_FILE" "$HTMX_HASH"
download_and_verify "$SWAGGER_JS_URL" "$SWAGGER_JS_FILE" "$SWAGGER_JS_HASH"
download_and_verify "$SWAGGER_CSS_URL" "$SWAGGER_CSS_FILE" "$SWAGGER_CSS_HASH"
download_and_verify "$MERMAID_JS_URL" "$MERMAID_JS_FILE" "$MERMAID_JS_HASH"
download_and_verify "$DATASTAR_JS_URL" "$DATASTAR_JS_FILE" "$DATASTAR_JS_HASH"

RESOURCES_DIR="$ORIGINAL_RESOURCES_DIR"

echo "----------------------------------------"
echo "🎉 All resources securely vendored."

echo "📦 Building editdistance wheel from specific GitHub commit..."
EDITDISTANCE_URL="${EDITDISTANCE_GIT_URL:-git+https://github.com/roy-ht/editdistance.git@3f5a5b0299f36662349df0917352a42c620e3dd4}"
python -m pip wheel "$EDITDISTANCE_URL" -w ./resources/wheels/
