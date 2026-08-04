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

echo "----------------------------------------"
echo "🎉 All resources securely vendored into $RESOURCES_DIR."

echo "📦 Building editdistance wheel from specific GitHub commit..."
EDITDISTANCE_URL="${EDITDISTANCE_GIT_URL:-git+https://github.com/roy-ht/editdistance.git@3f5a5b0299f36662349df0917352a42c620e3dd4}"
python -m pip wheel "$EDITDISTANCE_URL" -w ./resources/wheels/
