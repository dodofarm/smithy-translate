#!/usr/bin/env python3
"""Validate generated 3.1 fixtures against OAS structure and JSON Schema syntax."""

import argparse
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path)
    parser.add_argument("oas_schema", type=Path, help="Official OAS 3.1 schema/2025-11-23 JSON")
    args = parser.parse_args()
    validator = Draft202012Validator(json.loads(args.oas_schema.read_text()))
    failures = []
    count = 0

    def check_schema(schema, file, path):
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            failures.append(f"{file.name}: {path}: {error.message}")

    def walk(value, file, path=""):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "schema":
                    check_schema(child, file, path + "/schema")
                elif key == "schemas" and path == "/components":
                    for name, schema in child.items():
                        check_schema(schema, file, path + "/schemas/" + name)
                else:
                    walk(child, file, path + "/" + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, file, path + "/" + str(index))

    for file in sorted(args.inputs.glob("*.json")):
        document = json.loads(file.read_text())
        if not document.get("openapi", "").startswith("3.1."):
            continue
        count += 1
        for error in validator.iter_errors(document):
            failures.append(f"{file.name}: {error.json_path}: {error.message}")
        walk(document, file)
    print(f"Checked {count} OpenAPI 3.1 documents; {len(failures)} schema errors.")
    print("Checks cover document structure and schema syntax, not exhaustive reference/dialect semantics.")
    for failure in failures:
        print(failure)
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
