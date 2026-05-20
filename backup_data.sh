#!/bin/bash
# Strict mode: abort on any error
set -e

# Generate timestamp and short git commit hash
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
GIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "nogit")

BACKUP_DIR_NAME="verbal_backup_${TIMESTAMP}_${GIT_HASH}"
STAGING_DIR="/tmp/${BACKUP_DIR_NAME}"

echo "🚀 Starting Verbal data backup..."

# Create a temporary staging area
mkdir -p "$STAGING_DIR"

# 1. Database Dump
DB_DUMP_FILE="db_${TIMESTAMP}_${GIT_HASH}.json"
echo "📦 Dumping database to ${DB_DUMP_FILE}..."
python manage.py dumpdata --indent=2 \
  --exclude grips.celerystatus \
  --exclude grobid_client.citationgraphexplorer \
  -o "${STAGING_DIR}/${DB_DUMP_FILE}"

# Helper function to copy folders if they exist
copy_if_exists() {
    local src_dir=$1
    if [ -d "$src_dir" ]; then
        echo "📂 Copying ${src_dir}..."
        # Create the matching directory structure inside the staging area
        mkdir -p "${STAGING_DIR}/${src_dir}"
        cp -r "$src_dir/"* "${STAGING_DIR}/${src_dir}/" 2>/dev/null || true
    else
        echo "⚠️ Directory not found: ${src_dir} (Skipping)"
    fi
}

# 2. Documents Folder
# Note: Django's `upload_to='documents/'` usually resolves to `media/documents`
# based on standard MEDIA_ROOT configurations.
copy_if_exists "documents"

# Also grab the extracted zip corpora if they exist
copy_if_exists "corpora"

# 3. Background Resources Vector & Chunk Stores
copy_if_exists "background_resources/vector_store"
copy_if_exists "background_resources/vector_store/chunks"

# 4. Grips Vector Store
copy_if_exists "grips/vector_store"

# 5. Compress to Zip
ZIP_FILE="${BACKUP_DIR_NAME}.zip"
echo "🗜️ Compressing backup into ${ZIP_FILE}..."

# Move to /tmp to zip so the internal paths stay clean (without the /tmp prefix)
cd /tmp
zip -r "$ZIP_FILE" "$BACKUP_DIR_NAME" > /dev/null
cd - > /dev/null

# Move the finished zip back to the project root and clean up the staging area
mv "/tmp/${ZIP_FILE}" .
rm -rf "$STAGING_DIR"

echo "✅ Backup complete! Archive saved as: ${ZIP_FILE}"
