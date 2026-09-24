#!/bin/bash
# =============================================================================
# Verbal Project — Dependency Management & Virtual Environment Sync
# =============================================================================

# Safety check: Prevent sourcing which can close the user's terminal on errors or exit
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    echo "❌ Error: Do not source this script (e.g. '. update_dependencies.sh' or 'source update_dependencies.sh')." >&2
    echo "   Run it directly in your shell: bash update_dependencies.sh [options]" >&2
    return 1 2>/dev/null || exit 1
fi

# Ensure compilers are available if installed in standard paths
if [ -x "/usr/bin/gcc" ]; then export CC=/usr/bin/gcc; fi
if [ -x "/usr/bin/g++" ]; then export CXX=/usr/bin/g++; fi

# Default options
DO_COMPILE=true
DO_SYNC=true
ALLOW_UPGRADE=true
GENERATE_HASHES=true
USE_PYTORCH_INDEX=auto
CUSTOM_PYTORCH_URL=""
CUSTOM_INDEX_URL=""
CUSTOM_TRUSTED_HOST=""
CUSTOM_EXTRA_INDEX_URL=""
CUSTOM_CONSTRAINTS=""
CUSTOM_PYTHON=""
INSTALL_PLAYWRIGHT=true

usage() {
    cat << 'EOF'
Usage: bash update_dependencies.sh [OPTIONS]

Compile requirements.in -> requirements.txt and sync the virtual environment.

Options:
  --no-pytorch-index, --no-pytorch, --cpu
                               Do not add the external PyTorch extra-index URL.
                               Recommended when using internal mirrors (Artifactory)
                               or standard PyPI/CPU deployments.
  --pytorch-url <URL>          Explicit PyTorch wheel index URL.
  --no-upgrade                 Do NOT force upgrading all dependencies to latest versions.
                               Uses existing requirements.txt pins as preference/constraints.
                               Crucial if Artifactory blocks bleeding-edge package releases.
  --no-hashes                  Omit '--generate-hashes'.
                               Useful if internal Artifactory mirrors serve modified/unhashed wheels.
  --sync-only, --deploy        Skip compilation; sync virtual environment directly from
                               the existing requirements.txt (fast, offline/deployment mode).
  --compile-only               Only compile requirements.in -> requirements.txt without syncing.
  --index-url <URL>            Package index URL (overrides PIP_INDEX_URL in .env).
  --extra-index-url <URL>      Additional package index URL.
  --trusted-host <HOST>        Allow insecure / self-signed host (overrides PIP_TRUSTED_HOST).
  --constraint <FILE>          Apply a constraints file during compilation.
  --python <PATH>              Path to target python interpreter.
  --skip-playwright            Skip Playwright browser binary installation.
  -h, --help                   Show this help message.

Environment Variables (from .env or shell):
  PIP_INDEX_URL                Internal Artifactory / PyPI index URL.
  PIP_TRUSTED_HOST             Trusted host for private mirrors.
  PYTORCH_INDEX_URL            PyTorch wheel repository. Set to "none", "off", or "" to disable.
  PYENV_ACTIVATE               Path to virtualenv activate script.
EOF
    exit 0
}

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-pytorch-index|--no-pytorch|--cpu)
            USE_PYTORCH_INDEX=false
            shift
            ;;
        --pytorch-url)
            CUSTOM_PYTORCH_URL="$2"
            shift 2
            ;;
        --no-upgrade)
            ALLOW_UPGRADE=false
            shift
            ;;
        --no-hashes)
            GENERATE_HASHES=false
            shift
            ;;
        --sync-only|--deploy)
            DO_COMPILE=false
            DO_SYNC=true
            shift
            ;;
        --compile-only)
            DO_COMPILE=true
            DO_SYNC=false
            shift
            ;;
        --index-url)
            CUSTOM_INDEX_URL="$2"
            shift 2
            ;;
        --extra-index-url)
            CUSTOM_EXTRA_INDEX_URL="$2"
            shift 2
            ;;
        --trusted-host)
            CUSTOM_TRUSTED_HOST="$2"
            shift 2
            ;;
        --constraint)
            CUSTOM_CONSTRAINTS="$2"
            shift 2
            ;;
        --python)
            CUSTOM_PYTHON="$2"
            shift 2
            ;;
        --skip-playwright)
            INSTALL_PLAYWRIGHT=false
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "❌ Unknown argument: $1" >&2
            echo "   Run 'bash update_dependencies.sh --help' for usage." >&2
            exit 1
            ;;
    esac
done

# Load environment configuration (.env takes precedence, fallback to .env.example)
if [ -f .env ]; then
    . .env
elif [ -f .env.example ]; then
    echo "ℹ️  Notice: .env file not found, using .env.example defaults."
    . .env.example
fi

# Locate Python binary
PYTHON_BIN=""
if [ -n "$CUSTOM_PYTHON" ]; then
    PYTHON_BIN="$CUSTOM_PYTHON"
elif [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
elif [ -n "$PYENV_ACTIVATE" ]; then
    ACT_DIR="$(dirname "$PYENV_ACTIVATE")"
    if [ -x "$ACT_DIR/python" ]; then
        PYTHON_BIN="$ACT_DIR/python"
    fi
fi

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    if [ -x "../../py312/bin/python" ]; then
        PYTHON_BIN="../../py312/bin/python"
    elif [ -x "../../py313/bin/python" ]; then
        PYTHON_BIN="../../py313/bin/python"
    elif [ -x ".venv/bin/python" ]; then
        PYTHON_BIN=".venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3)"
    else
        echo "❌ Error: Could not locate Python executable." >&2
        echo "   Please specify with '--python /path/to/bin/python' or set PYENV_ACTIVATE." >&2
        exit 1
    fi
fi

# Determine PyTorch Index URL
PYTORCH_ARG=""
if [ "$USE_PYTORCH_INDEX" = "false" ]; then
    echo "ℹ️  PyTorch extra-index disabled by command line flag."
elif [ -n "$CUSTOM_PYTORCH_URL" ]; then
    PYTORCH_ARG="--extra-index-url $CUSTOM_PYTORCH_URL"
else
    PT_ENV="${PYTORCH_INDEX_URL:-https://download.pytorch.org/whl/cu124}"
    case "$PT_ENV" in
        none|off|false|disabled|"")
            echo "ℹ️  PyTorch extra-index disabled via PYTORCH_INDEX_URL ($PT_ENV)."
            ;;
        *)
            PYTORCH_ARG="--extra-index-url $PT_ENV"
            ;;
    esac
fi

# Determine Package Index & Trusted Host options
INDEX_ARGS=()
INDEX_URL="${CUSTOM_INDEX_URL:-$PIP_INDEX_URL}"
if [ -n "$INDEX_URL" ]; then
    INDEX_ARGS+=("--default-index" "$INDEX_URL")
fi

if [ -n "$CUSTOM_EXTRA_INDEX_URL" ]; then
    INDEX_ARGS+=("--extra-index-url" "$CUSTOM_EXTRA_INDEX_URL")
fi

TRUSTED_HOST="${CUSTOM_TRUSTED_HOST:-$PIP_TRUSTED_HOST}"
if [ -n "$TRUSTED_HOST" ]; then
    INDEX_ARGS+=("--allow-insecure-host" "$TRUSTED_HOST")
fi

# Determine Constraints
CONSTRAINT_ARGS=()
if [ -n "$CUSTOM_CONSTRAINTS" ]; then
    if [ -f "$CUSTOM_CONSTRAINTS" ]; then
        CONSTRAINT_ARGS+=("--constraint" "$CUSTOM_CONSTRAINTS")
    else
        echo "❌ Error: Constraint file '$CUSTOM_CONSTRAINTS' not found." >&2
        exit 1
    fi
elif [ -f "constraints.txt" ]; then
    echo "ℹ️  Detected local 'constraints.txt' — applying as constraint."
    CONSTRAINT_ARGS+=("--constraint" "constraints.txt")
fi

# -----------------------------------------------------------------------------
# Phase 1: Compile requirements.in -> requirements.txt
# -----------------------------------------------------------------------------
if [ "$DO_COMPILE" = "true" ]; then
    if [ ! -f "requirements.in" ]; then
        echo "❌ Error: requirements.in not found!" >&2
        echo "Please create a requirements.in file with your top-level dependencies." >&2
        exit 1
    fi

    # Backup the previous known-good requirements state before overwriting
    if [ -f requirements.txt ]; then
        echo "📋 Backing up current requirements.txt -> requirements_old.txt"
        cp requirements.txt requirements_old.txt
    fi

    COMPILE_ARGS=()
    if [ "$ALLOW_UPGRADE" = "true" ]; then
        COMPILE_ARGS+=("--upgrade")
    fi
    if [ "$GENERATE_HASHES" = "true" ]; then
        COMPILE_ARGS+=("--generate-hashes")
    fi
    if [ -n "$PYTORCH_ARG" ]; then
        COMPILE_ARGS+=($PYTORCH_ARG)
    fi
    if [ ${#INDEX_ARGS[@]} -gt 0 ]; then
        COMPILE_ARGS+=("${INDEX_ARGS[@]}")
    fi
    if [ ${#CONSTRAINT_ARGS[@]} -gt 0 ]; then
        COMPILE_ARGS+=("${CONSTRAINT_ARGS[@]}")
    fi
    COMPILE_ARGS+=("--emit-index-url")

    echo "🔄 1/2: Compiling requirements.in -> requirements.txt..."
    echo "   Command: uv pip compile requirements.in -o requirements.txt ${COMPILE_ARGS[*]} --python $PYTHON_BIN"

    COMPILE_LOG="$(mktemp 2>/dev/null || echo "compile_error.log")"
    if uv pip compile requirements.in -o requirements.txt "${COMPILE_ARGS[@]}" --python "$PYTHON_BIN" > "$COMPILE_LOG" 2>&1; then
        cat "$COMPILE_LOG"
        rm -f "$COMPILE_LOG"
        echo "✅ Compilation successful."
    else
        EXIT_CODE=$?
        cat "$COMPILE_LOG"
        echo ""
        echo "================================================================================"
        echo "❌ UV PIP COMPILE FAILED (Exit Code: $EXIT_CODE)"
        echo "================================================================================"

        # Diagnose known failure modes
        if grep -qi "403" "$COMPILE_LOG" || grep -qi "forbidden" "$COMPILE_LOG"; then
            AFFECTED_PKG="$(grep -E "(403|Forbidden|distributions for)" "$COMPILE_LOG" | head -n 3 | tr '\n' ' ')"
            echo "🚨 DIAGNOSIS: Artifactory / Proxy returned HTTP 403 Forbidden."
            [ -n "$AFFECTED_PKG" ] && echo "   Detail: $AFFECTED_PKG"
            echo ""
            echo "   Why this happens:"
            echo "     - In enterprise Artifactory environments (e.g. JFrog Xray / Curation),"
            echo "       newly released packages (e.g. sentence-transformers 6.0.x) or unvetted"
            echo "       major versions are blocked by security/governance policies."
            echo "     - Or your Artifactory index requires authentication credentials."
            echo ""
            echo "   RECOMMENDED REMEDIES:"
            echo "     Option A: Re-run with '--no-upgrade' to respect existing proven lockfile versions:"
            echo "        bash update_dependencies.sh --no-upgrade"
            echo ""
            echo "     Option B: Deploy directly using the existing tested requirements.txt without compiling:"
            echo "        bash update_dependencies.sh --sync-only"
            echo ""
            echo "     Option C: Add a temporary constraint for the affected package without modifying requirements.in:"
            echo "        echo 'sentence-transformers<6.0.0' >> constraints.txt"
            echo "        bash update_dependencies.sh"
            echo ""
            echo "     Option D: If Artifactory requires authentication, set credentials in PIP_INDEX_URL in .env:"
            echo "        PIP_INDEX_URL=https://<user>:<token-or-key>@artifactory.local/api/pypi/simple"
        elif grep -qi "download.pytorch.org" "$COMPILE_LOG" || grep -qi "connection refused" "$COMPILE_LOG" || grep -qi "failed to connect" "$COMPILE_LOG"; then
            echo "🚨 DIAGNOSIS: PyTorch index connection failed."
            echo ""
            echo "   Why this happens:"
            echo "     - The host cannot reach download.pytorch.org (firewall, air-gap, or offline)."
            echo ""
            echo "   RECOMMENDED REMEDIES:"
            echo "     1. Disable the PyTorch extra index:"
            echo "        bash update_dependencies.sh --no-pytorch-index"
            echo "     2. Or set in your .env:"
            echo "        PYTORCH_INDEX_URL=none"
        elif grep -qi "hash" "$COMPILE_LOG"; then
            echo "🚨 DIAGNOSIS: Cryptographic hash mismatch or missing hashes from mirror."
            echo ""
            echo "   RECOMMENDED REMEDY:"
            echo "     Run without hash verification:"
            echo "        bash update_dependencies.sh --no-hashes"
        else
            echo "🚨 DIAGNOSIS: Dependency resolution failed."
            echo "   Check the compiler output above for specific conflicting version requirements."
            echo "   Try running with '--no-upgrade' or using a constraints file."
        fi

        # Restore requirements.txt if a backup exists
        if [ -f requirements_old.txt ]; then
            echo ""
            echo "ℹ️  Restoring original requirements.txt from requirements_old.txt"
            cp requirements_old.txt requirements.txt
        fi
        rm -f "$COMPILE_LOG"
        echo "================================================================================"
        exit $EXIT_CODE
    fi
fi

# -----------------------------------------------------------------------------
# Phase 2: Sync Virtual Environment
# -----------------------------------------------------------------------------
if [ "$DO_SYNC" = "true" ]; then
    if [ ! -f requirements.txt ]; then
        echo "❌ Error: requirements.txt not found! Please compile first or provide requirements.txt." >&2
        exit 1
    fi

    SYNC_ARGS=()
    if [ -n "$PYTORCH_ARG" ]; then
        SYNC_ARGS+=($PYTORCH_ARG)
    fi
    if [ ${#INDEX_ARGS[@]} -gt 0 ]; then
        SYNC_ARGS+=("${INDEX_ARGS[@]}")
    fi

    echo "📦 2/2: Syncing virtual environment ($PYTHON_BIN)..."
    echo "   Command: uv pip sync requirements.txt ${SYNC_ARGS[*]} --python $PYTHON_BIN"

    SYNC_LOG="$(mktemp 2>/dev/null || echo "sync_error.log")"
    if uv pip sync requirements.txt "${SYNC_ARGS[@]}" --python "$PYTHON_BIN" > "$SYNC_LOG" 2>&1; then
        cat "$SYNC_LOG"
        rm -f "$SYNC_LOG"
        echo "----------------------------------------"
        echo "✅ Success! Dependencies synced cleanly into virtual environment."
    else
        EXIT_CODE=$?
        cat "$SYNC_LOG"
        echo ""
        echo "================================================================================"
        echo "❌ UV PIP SYNC FAILED (Exit Code: $EXIT_CODE)"
        echo "================================================================================"
        if grep -qi "hash" "$SYNC_LOG"; then
            echo "🚨 Hash mismatch detected during download from your package mirror."
            echo "   Try re-compiling with: bash update_dependencies.sh --no-hashes"
        elif grep -qi "403" "$SYNC_LOG" || grep -qi "forbidden" "$SYNC_LOG"; then
            echo "🚨 Artifactory returned HTTP 403 Forbidden during wheel download."
            echo "   Check that PIP_INDEX_URL in .env contains valid credentials and that your"
            echo "   user/token has read permissions on all repository wheels."
        fi
        rm -f "$SYNC_LOG"
        echo "================================================================================"
        exit $EXIT_CODE
    fi

    if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then
        echo "🎭 Attempting to install Playwright browser binaries..."
        "$PYTHON_BIN" -m playwright install chromium || echo "⚠️ Playwright not installed or unavailable, skipping browser installation."
    fi
fi
