#!/bin/bash
# Strict mode: abort on any error
# ensure compilers are available
# sudo apt-get update
# sudo apt-get install --reinstall build-essential gcc g++ python3.13-dev

export CC=/usr/bin/gcc
export CXX=/usr/bin/g++

set -e

if [ ! -f "requirements.in" ]; then
    echo "❌ Error: requirements.in not found!"
    echo "Please create a requirements.in file with your top-level dependencies."
    exit 1
fi

# Load environment configuration
if [ -f .env ]; then
    . .env
else
    echo "Warning: .env file not found, using .env.example"
    . .env.example
fi

PYTORCH_URL="${PYTORCH_INDEX_URL:-https://download.pytorch.org/whl/cu124}"
if [ -n "$PYTORCH_URL" ]; then
    PYTORCH_INDEX="--extra-index-url $PYTORCH_URL"
else
    PYTORCH_INDEX=""
fi

 # Backup the previous known-good requirements state before overwriting
if [ -f requirements.txt ]; then
  echo "moving current requirements.txt to requirements_old.txt"
  cp requirements.txt requirements_old.txt
fi


echo "🔄 1/2: Compiling requirements.in -> requirements.txt..."
# --upgrade tells uv to ignore the old requirements.txt and fetch the latest
# versions of everything that satisfy your requirements.in constraints.
# --generate-hashes adds an extra layer of supply-chain security by locking
# the cryptographic hash of every downloaded dependency.
uv pip compile requirements.in -o requirements.txt --upgrade --generate-hashes $PYTORCH_INDEX --emit-index-url

echo "📦 2/2: Syncing virtual environment..."
# 'sync' is incredibly powerful. It doesn't just install missing packages;
# it will actively uninstall packages in your virtual environment that are NOT
# in your requirements.txt, keeping your environment perfectly clean!
uv pip sync requirements.txt $PYTORCH_INDEX

echo "----------------------------------------"
echo "✅ Success! Dependencies updated and locked."
echo "Don't forget to commit your updated requirements.txt to source control."
