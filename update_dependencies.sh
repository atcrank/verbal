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

# Add ~/.local/bin to PATH in case uv is installed there
if [ -d "$HOME/.local/bin" ]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# Default options
DO_COMPILE=true
DO_SYNC=true
ALLOW_UPGRADE=true
GENERATE_HASHES=true
USE_PYTORCH_INDEX=auto
CUSTOM_PYTORCH_URL=""
CUSTOM_INDEX_URL=""
CUSTOM_FIND_LINKS=""
CUSTOM_TRUSTED_HOST=""
CUSTOM_EXTRA_INDEX_URL=""
CUSTOM_CONSTRAINTS=""
CUSTOM_PYTHON=""
INSTALL_PLAYWRIGHT=true

usage() {
    cat << 'EOF'
Usage: bash update_dependencies.sh [OPTIONS]

Compile requirements.in -> requirements.txt and sync the virtual environment.

There are two primary environments:
  1. Open Internet (Default):
     Uses PyPI and official PyTorch CUDA extra-index.
  2. Closed Internet / Artifactory:
     Configured via PIP_INDEX_URL, PYTORCH_INDEX_URL, PIP_FIND_LINKS, and PIP_TRUSTED_HOST.

Options:
  --no-pytorch-index, --no-pytorch, --cpu
                               Do not add the external PyTorch extra-index URL.
                               Recommended when using internal mirrors (Artifactory)
                               or standard PyPI/CPU deployments.
  --pytorch-url <URL>          Explicit PyTorch wheel index URL (overrides PYTORCH_INDEX_URL).
  --index-url <URL>            Package index URL (overrides PIP_INDEX_URL in .env).
  --extra-index-url <URL>      Additional package index URL.
  --find-links <URL>           HTML wheel links directory (overrides PIP_FIND_LINKS in .env).
  --trusted-host <HOST>        Allow insecure / self-signed host (overrides PIP_TRUSTED_HOST).
  --no-upgrade                 Do NOT force upgrading all dependencies to latest versions.
                               Uses existing requirements.txt pins as preference/constraints.
                               Crucial if Artifactory blocks bleeding-edge package releases.
  --no-hashes                  Omit '--generate-hashes'.
                               Useful if internal Artifactory mirrors serve modified/unhashed wheels.
  --sync-only, --deploy        Skip compilation; sync virtual environment directly from
                               the existing requirements.txt (fast, deployment/offline mode).
  --compile-only               Only compile requirements.in -> requirements.txt without syncing.
  --constraint <FILE>          Apply a constraints file during compilation.
  --python <PATH>              Path to target python interpreter.
  --skip-playwright            Skip Playwright browser binary installation.
  -h, --help                   Show this help message.

Canonical Environment Variables (from .env or shell):
  PIP_INDEX_URL                Primary package index URL (e.g. Artifactory / PyPI mirror).
  PYTORCH_INDEX_URL            PyTorch wheel repository (defaults to CUDA 12.4; set to "none" to disable).
  PIP_FIND_LINKS               HTML wheel links directory / Artifactory wheel page.
  PIP_TRUSTED_HOST             Trusted host for private mirrors or self-signed SSL certs.
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
        --index-url)
            CUSTOM_INDEX_URL="$2"
            shift 2
            ;;
        --extra-index-url)
            CUSTOM_EXTRA_INDEX_URL="$2"
            shift 2
            ;;
        --find-links)
            CUSTOM_FIND_LINKS="$2"
            shift 2
            ;;
        --trusted-host)
            CUSTOM_TRUSTED_HOST="$2"
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
PYTORCH_URL=""
if [ "$USE_PYTORCH_INDEX" = "false" ]; then
    echo "ℹ️  PyTorch extra-index disabled by command line flag."
elif [ -n "$CUSTOM_PYTORCH_URL" ]; then
    PYTORCH_URL="$CUSTOM_PYTORCH_URL"
else
    PT_ENV="${PYTORCH_INDEX_URL:-https://download.pytorch.org/whl/cu124}"
    case "$PT_ENV" in
        none|off|false|disabled|"")
            echo "ℹ️  PyTorch extra-index disabled via PYTORCH_INDEX_URL ($PT_ENV)."
            ;;
        *)
            PYTORCH_URL="$PT_ENV"
            ;;
    esac
fi

# Resolve canonical package index & mirror parameters
INDEX_URL="${CUSTOM_INDEX_URL:-$PIP_INDEX_URL}"
TRUSTED_HOST="${CUSTOM_TRUSTED_HOST:-$PIP_TRUSTED_HOST}"
FIND_LINKS_URL="${CUSTOM_FIND_LINKS:-$PIP_FIND_LINKS}"
EXTRA_INDEX_URL="$CUSTOM_EXTRA_INDEX_URL"

# Detect tool availability
HAVE_UV=false
if command -v uv >/dev/null 2>&1; then
    HAVE_UV=true
fi

# Build uv argument lists
UV_INDEX_ARGS=()
if [ -n "$INDEX_URL" ]; then
    UV_INDEX_ARGS+=("--index-url" "$INDEX_URL")
fi
if [ -n "$EXTRA_INDEX_URL" ]; then
    UV_INDEX_ARGS+=("--extra-index-url" "$EXTRA_INDEX_URL")
fi
if [ -n "$PYTORCH_URL" ]; then
    UV_INDEX_ARGS+=("--extra-index-url" "$PYTORCH_URL")
fi
if [ -n "$FIND_LINKS_URL" ]; then
    UV_INDEX_ARGS+=("--find-links" "$FIND_LINKS_URL")
fi
if [ -n "$TRUSTED_HOST" ]; then
    UV_INDEX_ARGS+=("--allow-insecure-host" "$TRUSTED_HOST")
fi

# Build standard pip argument lists (used as fallback when uv is not available)
PIP_INDEX_ARGS=()
if [ -n "$INDEX_URL" ]; then
    PIP_INDEX_ARGS+=("--index-url" "$INDEX_URL")
fi
if [ -n "$EXTRA_INDEX_URL" ]; then
    PIP_INDEX_ARGS+=("--extra-index-url" "$EXTRA_INDEX_URL")
fi
if [ -n "$PYTORCH_URL" ]; then
    PIP_INDEX_ARGS+=("--extra-index-url" "$PYTORCH_URL")
fi
if [ -n "$FIND_LINKS_URL" ]; then
    PIP_INDEX_ARGS+=("--find-links" "$FIND_LINKS_URL")
fi
if [ -n "$TRUSTED_HOST" ]; then
    PIP_INDEX_ARGS+=("--trusted-host" "$TRUSTED_HOST")
fi

# Always include local vendored wheels directory if present
if [ -d "resources/wheels" ]; then
    UV_INDEX_ARGS+=("--find-links" "resources/wheels")
    PIP_INDEX_ARGS+=("--find-links" "resources/wheels")
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

    if [ "$HAVE_UV" = "true" ]; then
        COMPILE_ARGS=("--no-emit-index-url")
        if [ "$ALLOW_UPGRADE" = "true" ]; then
            COMPILE_ARGS+=("--upgrade")
        fi
        if [ "$GENERATE_HASHES" = "true" ]; then
            COMPILE_ARGS+=("--generate-hashes")
        fi
        if [ ${#UV_INDEX_ARGS[@]} -gt 0 ]; then
            COMPILE_ARGS+=("${UV_INDEX_ARGS[@]}")
        fi
        if [ ${#CONSTRAINT_ARGS[@]} -gt 0 ]; then
            COMPILE_ARGS+=("${CONSTRAINT_ARGS[@]}")
        fi

        echo "🔄 1/2: Compiling requirements.in -> requirements.txt via uv..."
        echo "   Command: uv pip compile requirements.in -o requirements.txt ${COMPILE_ARGS[*]} --python $PYTHON_BIN"

        COMPILE_LOG="$(mktemp 2>/dev/null || echo "compile_error.log")"
        if uv pip compile requirements.in -o requirements.txt "${COMPILE_ARGS[@]}" --python "$PYTHON_BIN" > "$COMPILE_LOG" 2>&1; then
            cat "$COMPILE_LOG"
            rm -f "$COMPILE_LOG"

            # Sanitization guarantee: ensure no un-commented index directives leaked into requirements.txt
            sed -i '/^[[:space:]]*--index-url/d' requirements.txt
            sed -i '/^[[:space:]]*--extra-index-url/d' requirements.txt

            echo "✅ Compilation successful (portable requirements.txt generated without baked-in index URLs)."
        else
            EXIT_CODE=$?
            cat "$COMPILE_LOG"
            echo ""
            echo "================================================================================"
            echo "❌ UV PIP COMPILE FAILED (Exit Code: $EXIT_CODE)"
            echo "================================================================================"
            # Restore requirements.txt if a backup exists
            if [ -f requirements_old.txt ]; then
                echo "ℹ️  Restoring original requirements.txt from requirements_old.txt"
                cp requirements_old.txt requirements.txt
            fi
            rm -f "$COMPILE_LOG"
            exit $EXIT_CODE
        fi
    elif "$PYTHON_BIN" -m piptools --version >/dev/null 2>&1 || command -v pip-compile >/dev/null 2>&1; then
        echo "🔄 1/2: 'uv' not found; compiling via pip-tools (pip-compile)..."
        PIP_COMPILE_BIN="pip-compile"
        if ! command -v pip-compile >/dev/null 2>&1; then
            PIP_COMPILE_BIN="$PYTHON_BIN -m piptools compile"
        fi
        COMPILE_ARGS=("--no-emit-index-url" "--output-file=requirements.txt")
        if [ "$ALLOW_UPGRADE" = "true" ]; then
            COMPILE_ARGS+=("--upgrade")
        fi
        if [ "$GENERATE_HASHES" = "true" ]; then
            COMPILE_ARGS+=("--generate-hashes")
        fi
        if [ ${#PIP_INDEX_ARGS[@]} -gt 0 ]; then
            COMPILE_ARGS+=("${PIP_INDEX_ARGS[@]}")
        fi
        if [ ${#CONSTRAINT_ARGS[@]} -gt 0 ]; then
            COMPILE_ARGS+=("${CONSTRAINT_ARGS[@]}")
        fi
        $PIP_COMPILE_BIN "${COMPILE_ARGS[@]}" requirements.in
        sed -i '/^[[:space:]]*--index-url/d' requirements.txt
        sed -i '/^[[:space:]]*--extra-index-url/d' requirements.txt
        echo "✅ Compilation successful via pip-compile."
    else
        echo "❌ Error: Neither 'uv' nor 'pip-tools' is installed for requirements compilation." >&2
        echo "   Dependency compilation (requirements.in -> requirements.txt) is intended" >&2
        echo "   for development machines with 'uv' or 'pip-tools' installed." >&2
        echo "" >&2
        echo "   Remedies:" >&2
        echo "     - To install uv: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
        echo "     - Or on a deployed server, deploy/sync existing requirements.txt without compiling:" >&2
        echo "       bash update_dependencies.sh --sync-only" >&2
        exit 1
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

    if [ "$HAVE_UV" = "true" ]; then
        SYNC_ARGS=()
        if [ ${#UV_INDEX_ARGS[@]} -gt 0 ]; then
            SYNC_ARGS+=("${UV_INDEX_ARGS[@]}")
        fi

        echo "📦 2/2: Syncing virtual environment via uv ($PYTHON_BIN)..."
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
            rm -f "$SYNC_LOG"
            echo "❌ UV PIP SYNC FAILED (Exit Code: $EXIT_CODE)"
            exit $EXIT_CODE
        fi
    else
        echo "📦 2/2: 'uv' not found; syncing virtual environment using standard pip ($PYTHON_BIN)..."
        PIP_INSTALL_ARGS=("--no-cache-dir")
        if [ ${#PIP_INDEX_ARGS[@]} -gt 0 ]; then
            PIP_INSTALL_ARGS+=("${PIP_INDEX_ARGS[@]}")
        fi

        echo "   Command: $PYTHON_BIN -m pip install ${PIP_INSTALL_ARGS[*]} -r requirements.txt"
        if "$PYTHON_BIN" -m pip install "${PIP_INSTALL_ARGS[@]}" -r requirements.txt; then
            echo "----------------------------------------"
            echo "✅ Success! Dependencies installed cleanly via pip."
        else
            EXIT_CODE=$?
            echo "❌ PIP INSTALL FAILED (Exit Code: $EXIT_CODE)"
            exit $EXIT_CODE
        fi
    fi

    if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then
        echo "🎭 Attempting to install Playwright browser binaries..."
        "$PYTHON_BIN" -m playwright install chromium || echo "⚠️ Playwright not installed or unavailable, skipping browser installation."
    fi
fi
