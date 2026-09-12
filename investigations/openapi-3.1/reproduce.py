#!/usr/bin/env python3
"""Assert issue #306's scalar targets, using an explicitly supplied CLI classpath."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def issue_document(version):
    return {
        "openapi": version,
        "info": {"title": "Scalar reproduction", "version": "1.0.0"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{
                        "name": "id", "in": "query", "required": True,
                        "schema": {"type": "string"},
                    }],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "count": {"type": "integer"},
                                            "active": {"type": "boolean"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classpath-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / ".cache/openapi-3.1/repro")
    args = parser.parse_args()
    classpath = args.classpath_file.read_text().strip()
    java = str(Path(os.environ["JAVA_HOME"]) / "bin/java") if "JAVA_HOME" in os.environ else "java"
    expected = {
        "openapi#GetItemInput$id": "smithy.api#String",
        "openapi#Body$name": "smithy.api#String",
        "openapi#Body$count": "smithy.api#Integer",
        "openapi#Body$active": "smithy.api#Boolean",
    }
    failures = 0
    for version in ("3.0.3", "3.1.0"):
        case_dir = args.output.resolve() / version
        case_dir.mkdir(parents=True, exist_ok=True)
        source = case_dir / "openapi.json"
        source.write_text(json.dumps(issue_document(version), indent=2) + "\n")
        output = case_dir / "smithy"
        output.mkdir(exist_ok=True)
        # Clear only this probe's generated JSON so a previous success cannot mask failure.
        for old in output.glob("*.json"):
            old.unlink()
        result = subprocess.run([
            java, "-cp", classpath, "smithytranslate.cli.Main", "openapi-to-smithy",
            "--input", str(source), "--validate-input", "--json-output", str(output),
        ], text=True, capture_output=True, timeout=60)
        (case_dir / "cli.log").write_text(result.stdout + result.stderr)
        shapes = {}
        for path in output.glob("*.json"):
            shapes.update(json.loads(path.read_text()).get("shapes", {}))
        print(f"OpenAPI {version}: CLI exit {result.returncode}")
        for member, target in expected.items():
            shape, name = member.split("$")
            actual = shapes.get(shape, {}).get("members", {}).get(name, {}).get("target")
            ok = actual == target
            print(f"  {'PASS' if ok else 'FAIL'} {member}: {actual} (expected {target})")
            failures += not ok
    print(f"{failures} target assertion(s) failed; fixtures, models and logs: {args.output}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
