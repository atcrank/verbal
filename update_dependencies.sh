#!/bin/bash
# Strict mode: abort on any error
set -e

if [ ! -f "requirements.in" ]; then
    echo "❌ Error: requirements.in not found!"
    echo "Please create a requirements.in file with your top-level dependencies."
    exit 1
fi

echo "🔄 1/2: Compiling requirements.in -> requirements.txt..."
# --upgrade tells uv to ignore the old requirements.txt and fetch the latest
# versions of everything that satisfy your requirements.in constraints.
# --generate-hashes adds an extra layer of supply-chain security by locking
# the cryptographic hash of every downloaded dependency.
uv pip compile requirements.in -o requirements.txt --upgrade --generate-hashes

echo "📦 2/2: Syncing virtual environment..."
# 'sync' is incredibly powerful. It doesn't just install missing packages;
# it will actively uninstall packages in your virtual environment that are NOT
# in your requirements.txt, keeping your environment perfectly clean!
uv pip sync requirements.txt

echo "----------------------------------------"
echo "✅ Success! Dependencies updated and locked."
echo "Don't forget to commit your updated requirements.txt to source control."
