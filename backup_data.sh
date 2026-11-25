#!/bin/bash
# Strict mode: abort on any error
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

if [ -f .env ]; then
    . .env
fi
if [ -f ~/.verbal_secrets ]; then
    . ~/.verbal_secrets
elif [ -f .env.secrets ]; then
    . .env.secrets
fi
[ -z "$HF_ENDPOINT" ] && unset HF_ENDPOINT
[ -z "$PIP_INDEX_URL" ] && unset PIP_INDEX_URL

if [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
elif [ -x "../../py312/bin/python" ]; then
    PYTHON_BIN="../../py312/bin/python"
else
    PYTHON_BIN="python"
fi

# Generate timestamp and short git commit hash
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "nogit")

BACKUP_DIR_NAME="verbal_backup_${TIMESTAMP}_${GIT_HASH}"
STAGING_DIR="/tmp/${BACKUP_DIR_NAME}"
trap 'rm -rf "$STAGING_DIR"' EXIT

echo "🚀 Starting Verbal data backup..."

# Create a temporary staging area
mkdir -p "$STAGING_DIR"

# 1. Database Dump (includes PGVector embeddings stored in RAGChunk and ConceptNode)
DB_DUMP_FILE="db_${TIMESTAMP}_${GIT_HASH}.json"
echo "📦 Dumping database to ${DB_DUMP_FILE}..."
"$PYTHON_BIN" manage.py dumpdata --indent=2 \
  --natural-foreign --natural-primary \
  --exclude contenttypes \
  --exclude auth.permission \
  --exclude grobid_client.citationgraphexplorer \
  -o "${STAGING_DIR}/${DB_DUMP_FILE}"

# Helper function to copy folders if they exist and are non-empty
copy_if_exists() {
    local src_dir=$1
    if [ -d "$src_dir" ] && [ -n "$(ls -A "$src_dir" 2>/dev/null)" ]; then
        echo "📂 Copying ${src_dir}..."
        mkdir -p "${STAGING_DIR}/${src_dir}"
        cp -a "$src_dir/." "${STAGING_DIR}/${src_dir}/"
    fi
}

# 2. Media Uploads, Extracted Figures/Corpora & LoRA Adapters
copy_if_exists "media/documents"
copy_if_exists "media/corpora"
copy_if_exists "documents"
copy_if_exists "lora_adapters"

# 3. Compress to Zip
ZIP_FILE="${BACKUP_DIR_NAME}.zip"
echo "🗜️ Compressing backup into ${ZIP_FILE}..."

(cd /tmp && zip -rq "$ZIP_FILE" "$BACKUP_DIR_NAME")
mv "/tmp/${ZIP_FILE}" .
rm -rf "$STAGING_DIR"

echo "✅ Backup complete! Archive saved as: ${ZIP_FILE}"
