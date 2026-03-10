#!/usr/bin/env bash
# opsec-check.sh — Journalist-specific opsec checks for RadianceFleet
# Covers identity/metadata leaks that gitleaks doesn't catch.
# Exit code: 0 if all pass, 1 if any FAIL.

set -euo pipefail

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

pass()  { echo "  PASS: $1"; ((PASS_COUNT++)); }
fail()  { echo "  FAIL: $1"; ((FAIL_COUNT++)); }
warn()  { echo "  WARN: $1"; ((WARN_COUNT++)); }

echo "=== RadianceFleet Journalist Opsec Check ==="
echo ""

# ---------------------------------------------------------------------------
# 1. Git author emails — all should be noreply (identity protection)
# ---------------------------------------------------------------------------
echo "[1/4] Git author email check"

NON_NOREPLY=$(git log --all --format='%ae' | sort -u | grep -v 'noreply' || true)
if [ -z "$NON_NOREPLY" ]; then
    pass "All commit author emails contain 'noreply'"
else
    fail "Non-noreply author emails found in git history:"
    echo "$NON_NOREPLY" | while read -r email; do
        echo "       - $email"
    done
fi

echo ""

# ---------------------------------------------------------------------------
# 2. No office document metadata (.docx/.xlsx contain author names)
# ---------------------------------------------------------------------------
echo "[2/4] Office document metadata check"

OFFICE_FILES=$(git ls-files -- '*.docx' '*.xlsx' '*.pptx' '*.doc' '*.xls' '*.ppt' '*.odt' '*.ods' '*.odp' 2>/dev/null || true)
if [ -z "$OFFICE_FILES" ]; then
    pass "No office documents tracked in git"
else
    fail "Office documents found (may contain author metadata):"
    echo "$OFFICE_FILES" | while read -r f; do
        echo "       - $f"
    done
fi

echo ""

# ---------------------------------------------------------------------------
# 3. Image EXIF metadata check (GPS/device info leak)
# ---------------------------------------------------------------------------
echo "[3/4] Image EXIF metadata check"

IMAGE_FILES=$(git ls-files -- '*.png' '*.jpg' '*.jpeg' '*.tiff' '*.gif' '*.bmp' '*.webp' '*.heic' 2>/dev/null || true)
if [ -z "$IMAGE_FILES" ]; then
    pass "No image files tracked in git"
else
    if command -v exiftool &>/dev/null; then
        EXIF_ISSUES=""
        while IFS= read -r img; do
            # Check for GPS data or identifying device info
            GPS=$(exiftool -GPSLatitude -GPSLongitude -s3 "$img" 2>/dev/null || true)
            DEVICE=$(exiftool -Make -Model -Software -s3 "$img" 2>/dev/null || true)
            if [ -n "$GPS" ] || [ -n "$DEVICE" ]; then
                EXIF_ISSUES="$EXIF_ISSUES\n       - $img"
                [ -n "$GPS" ] && EXIF_ISSUES="$EXIF_ISSUES (GPS: $GPS)"
                [ -n "$DEVICE" ] && EXIF_ISSUES="$EXIF_ISSUES (Device: $DEVICE)"
            fi
        done <<< "$IMAGE_FILES"

        if [ -z "$EXIF_ISSUES" ]; then
            pass "No EXIF metadata (GPS/device) found in tracked images"
        else
            fail "Images with identifying EXIF metadata:$EXIF_ISSUES"
        fi
    else
        warn "exiftool not installed — cannot check image EXIF metadata"
        echo "       Install with: sudo apt install libimage-exiftool-perl"
    fi
fi

echo ""

# ---------------------------------------------------------------------------
# 4. No real vessel/analyst names in test fixtures
# ---------------------------------------------------------------------------
echo "[4/4] Test fixture identity check"

# Common real shadow fleet vessel names and analyst-sounding real names
# that should never appear in test data
SUSPECT_PATTERNS=(
    "Sovcomflot"
    "Gatik Ship"
    "Sun Ship Management"
    "Volga-Balt"
    "Novoship"
    "Primorsk"
    "@gmail\.com"
    "@yahoo\.com"
    "@hotmail\.com"
    "@outlook\.com"
    "@protonmail\.com"
)

TEST_DIRS="backend/tests frontend/src/__tests__ e2e"
FIXTURE_ISSUES=""

for pattern in "${SUSPECT_PATTERNS[@]}"; do
    for dir in $TEST_DIRS; do
        if [ -d "$dir" ]; then
            MATCHES=$(grep -rl "$pattern" "$dir" 2>/dev/null || true)
            if [ -n "$MATCHES" ]; then
                FIXTURE_ISSUES="$FIXTURE_ISSUES\n       - Pattern '$pattern' found in:"
                echo "$MATCHES" | while read -r f; do
                    FIXTURE_ISSUES="$FIXTURE_ISSUES\n         $f"
                done
            fi
        fi
    done
done

if [ -z "$FIXTURE_ISSUES" ]; then
    pass "No real vessel/analyst names or personal emails in test fixtures"
else
    warn "Possible real names/emails in test fixtures:$FIXTURE_ISSUES"
    echo "       Review these and replace with fictional data if needed"
fi

echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "=== Summary ==="
echo "  PASS: $PASS_COUNT"
echo "  FAIL: $FAIL_COUNT"
echo "  WARN: $WARN_COUNT"
echo ""

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "RESULT: FAILED — $FAIL_COUNT check(s) need attention"
    exit 1
else
    if [ "$WARN_COUNT" -gt 0 ]; then
        echo "RESULT: PASSED with $WARN_COUNT warning(s)"
    else
        echo "RESULT: ALL CLEAR"
    fi
    exit 0
fi
