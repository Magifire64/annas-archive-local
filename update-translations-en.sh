#!/bin/bash

set -Eeuxo pipefail

# Convert the source HTML files into the translatable versions
./bin/translate-html --in-place ./allthethings/**/templates/**/*.html.j2

# Some of these change their output when run multiple times
# TODO: --sort-output, to sort by msgid instead of file
pybabel extract --no-location --omit-header --mapping-file="babel.cfg" --output-file="messages.pot" .
pybabel update --locale="en" --no-wrap --omit-header --input-file="messages.pot" --output-dir="allthethings/translations" --no-fuzzy-matching
pybabel compile --locale="en" --use-fuzzy --directory allthethings/translations
