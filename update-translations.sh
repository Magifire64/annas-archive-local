#!/bin/bash

set -Eeuxo pipefail

# Convert the source HTML files into the translatable versions
./run cmd bin/translate-html --in-place ./allthethings/**/templates/**/*.html.j2

# Some of these change their output when run multiple times
for _ in 1 2 3
do
	pybabel extract --no-location --omit-header --mapping-file="babel.cfg" --output-file="messages.pot" .
	pybabel update --no-wrap --omit-header --input-file="messages.pot" --output-dir="allthethings/translations" --no-fuzzy-matching
	pybabel compile --use-fuzzy --directory allthethings/translations
done
