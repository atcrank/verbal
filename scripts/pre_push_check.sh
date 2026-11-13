#!/usr/bin/env bash
# Pre-Push & Pre-Deployment Validation Gatekeeper for Reason / Verbal
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if [ -n "$VIRTUAL_ENV" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
elif [ -f "../../py312/bin/python" ]; then
    PYTHON_BIN="../../py312/bin/python"
else
    PYTHON_BIN="python"
fi

echo "=========================================================="
echo "🛡️  REASON PRE-PUSH VALIDATION GATEWAY"
echo "=========================================================="

echo "🔍 1/4 Checking for pending uncreated Django migrations..."
$PYTHON_BIN manage.py makemigrations --check

echo "🔒 2/4 Checking Django deployment configuration (check --deploy)..."
$PYTHON_BIN manage.py check --deploy

echo "🧪 3/4 Running fast unit test suite (excluding e2e)..."
$PYTHON_BIN manage.py test --exclude-tag=e2e --noinput

echo "🧹 4/4 Checking git status for untracked binary build artifacts..."
UNTRACKED_BUILDS=$(git status --porcelain | grep -E "(documentation/build/|doctrees|\.pickle)" || true)
if [ -n "$UNTRACKED_BUILDS" ]; then
    echo "⚠️ Warning: Detected build artifacts in working tree:"
    echo "$UNTRACKED_BUILDS"
else
    echo "✅ Git working tree is clean of build artifacts."
fi

echo "=========================================================="
echo "🎉 ALL PRE-PUSH CHECKS PASSED SUCCESSFULLY!"
echo "=========================================================="

