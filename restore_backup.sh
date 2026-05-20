#!/bin/bash
# Strict mode: abort on any error
set -e

if [ -z "$1" ]; then
    echo "Usage: ./restore_backup.sh <path_to_backup_zip>"
    exit 1
fi

ZIP_FILE="$1"
if [ ! -f "$ZIP_FILE" ]; then
    echo "❌ Error: File '$ZIP_FILE' not found."
    exit 1
fi

STAGING_DIR="/tmp/verbal_restore_$(date +%s)"
mkdir -p "$STAGING_DIR"

echo "🚀 Starting Verbal data restore from ${ZIP_FILE}..."

echo "📦 Unzipping archive into staging area..."
unzip -q "$ZIP_FILE" -d "$STAGING_DIR"

# The zip contains a root folder like verbal_backup_YYYYMMDD_HHMMSS_hash
BACKUP_DIR=$(find "$STAGING_DIR" -maxdepth 1 -type d -name "verbal_backup_*" | head -n 1)

if [ -z "$BACKUP_DIR" ]; then
    echo "❌ Error: Could not find the backup directory inside the zip."
    rm -rf "$STAGING_DIR"
    exit 1
fi

# 1. Restore Database using loaddata
DB_DUMP_FILE=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "db_*.json" | head -n 1)

if [ -n "$DB_DUMP_FILE" ]; then
    echo "🧹 Sanitizing JSON dump: Removing NUL (\u0000) bytes for PostgreSQL compatibility..."
    # Postgres strictly forbids NUL bytes in text fields, but SQLite allows them. 
    # This strips the JSON-escaped null bytes from the dump.
    sed -i 's/\\u0000//g' "$DB_DUMP_FILE"

    echo "🧹 Clearing auto-generated ContentTypes to prevent ID conflicts..."
    # Delete contenttypes (which cascade deletes permissions) so loaddata can restore original IDs safely
    python manage.py shell -c "from django.contrib.contenttypes.models import ContentType; ContentType.objects.all().delete()"

    echo "🗄️ Loading database dump: $(basename "$DB_DUMP_FILE")..."
    python manage.py loaddata "$DB_DUMP_FILE"
else
    echo "⚠️ No database JSON dump found in the backup."
fi

# 2. Helper function to restore file directories
restore_dir() {
    local src_dir="$1"
    if [ -d "${BACKUP_DIR}/${src_dir}" ]; then
        echo "📂 Restoring ${src_dir}..."
        mkdir -p "${src_dir}"
        # Using -a (archive) to preserve file permissions and timestamps
        cp -a "${BACKUP_DIR}/${src_dir}/"* "${src_dir}/" 2>/dev/null || true
    fi
}

restore_dir "media/documents"
restore_dir "media/corpora"
restore_dir "background_resources/vector_store"
restore_dir "background_resources/chunk_store"
restore_dir "background_resources/chunks"
restore_dir "grips/vector_store"

echo "🧹 Cleaning up temporary files..."
rm -rf "$STAGING_DIR"

echo "✅ Restore complete! Your database and vector stores are ready."