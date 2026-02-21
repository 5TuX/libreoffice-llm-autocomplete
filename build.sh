#!/bin/bash
# Build the .oxt extension file
set -e
cd "$(dirname "$0")"
rm -f LLMAutocomplete.oxt
zip -r LLMAutocomplete.oxt \
    META-INF/ \
    description.xml \
    description-en.txt \
    description/ \
    Jobs.xcu \
    Factory.xcu \
    Sidebar.xcu \
    empty_dialog.xdl \
    images/ \
    python/
echo "Built LLMAutocomplete.oxt"
