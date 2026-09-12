#!/usr/bin/env python3
"""Generate isolated feature probes and run the real OpenAPI compiler in one JVM."""

import argparse
import copy
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from reproduce import issue_document


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FREE = {"type": "object", "additionalProperties": True}
STRUCT = {"type": "object", "properties": {"payload": FREE}}


def document(schemas=None):
    doc = {"openapi": "3.1.0", "info": {"title": "Feature probe", "version": "1"}, "paths": {}}
    if schemas is not None:
        doc["components"] = {"schemas": schemas}
    return doc


def operation(schema=FREE):
    return {
        "operationId": "getValue",
        "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": schema}}}},
    }


def cases():
    result = []

    def add(name, doc, versions=("3.0.3", "3.1.0"), control=None):
        for version in versions:
            value = copy.deepcopy(doc)
            value["openapi"] = version
            result.append({"name": name, "version": version, "document": value, "control": control})

    def schema(name, value, versions=("3.0.3", "3.1.0"), control=None):
        add(name, document({"Value": value}), versions, control)

    only31 = ("3.1.0",)
    add("issue-306", issue_document("3.1.0"))
    for kind in ("string", "integer", "number", "boolean"):
        schema("scalar-" + kind, {"type": kind})
    for kind, formats in {
        "string": ("date", "date-time", "uuid", "byte", "binary", "password", "email", "local-date"),
        "integer": ("int16", "int32", "int64"),
        "number": ("float", "double"),
    }.items():
        for fmt in formats:
            schema("format-" + fmt, {"type": kind, "format": fmt})
    schema("string-enum", {"type": "string", "enum": ["red", "blue"]})
    schema("integer-enum", {"type": "integer", "enum": [1, 2]})
    schema("string-constraints", {"type": "string", "minLength": 2, "maxLength": 5, "pattern": "^[a-z]+$"})
    schema("integer-range", {"type": "integer", "minimum": 1, "maximum": 5})
    schema("object-empty", {"type": "object"})
    schema("empty-schema", {}, only31)
    schema("object-free", FREE)
    schema("object-properties", STRUCT)
    schema("object-no-type", {"properties": {"payload": FREE}})
    schema("object-required", {**STRUCT, "required": ["payload"]})
    schema("object-closed", {**STRUCT, "additionalProperties": False})
    schema("object-closed-empty", {"type": "object", "additionalProperties": False})
    schema("object-description", {**STRUCT, "description": "Preserve this description"})
    schema("object-extension", {**STRUCT, "x-probe": "keep me"})
    schema("list", {"type": "array", "items": {"type": "string"}})
    schema("list-free-items", {"type": "array", "items": FREE})
    schema("set", {"type": "array", "uniqueItems": True, "items": {"type": "string"}})
    schema("typed-map", {"type": "object", "additionalProperties": {"type": "string"}})
    schema("typed-map-free-values", {"type": "object", "additionalProperties": STRUCT})
    schema("object-and-typed-extras", {**STRUCT, "additionalProperties": {"type": "string"}})
    for keyword in ("allOf", "oneOf", "anyOf"):
        schema(keyword, {keyword: [STRUCT, {"type": "object", "properties": {"other": FREE}}]})
        schema(keyword + "-with-properties", {**STRUCT, keyword: [{"type": "object", "properties": {"other": FREE}}]})
    add("local-schema-ref", document({"Base": STRUCT, "Value": {"type": "object", "properties": {"base": {"$ref": "#/components/schemas/Base"}}}}))

    schema("type-singleton-array", {"type": ["string"]}, only31)
    schema("nullable-string", {"type": ["string", "null"]}, only31)
    schema("nullable-object", {**STRUCT, "type": ["object", "null"]}, only31, "object-properties")
    schema("multi-type", {"type": ["string", "integer"]}, only31)
    schema("multi-type-with-properties", {**STRUCT, "type": ["object", "string"]}, only31, "object-properties")
    schema("null-type", {"type": "null"}, only31)
    schema("boolean-true", True, only31)
    schema("boolean-false", False, only31)
    schema("boolean-property", {"type": "object", "properties": {"payload": False}}, only31)
    schema("boolean-property-true", {"type": "object", "properties": {"payload": True}}, only31)
    schema("const-string", {"type": "string", "const": "fixed"}, only31)
    schema("const-only", {"const": "fixed"}, only31)
    schema("const-object", {**STRUCT, "const": {"payload": {}}}, only31, "object-properties")
    schema("numeric-exclusive-bounds", {"type": "integer", "exclusiveMinimum": 1, "exclusiveMaximum": 5}, only31)
    schema("schema-example", {**STRUCT, "example": {"payload": {}}})
    schema("schema-examples", {**STRUCT, "examples": [{"payload": {}}, {"payload": {"x": 1}}]}, only31, "object-properties")
    schema("binary-content-encoding", {"type": "string", "contentEncoding": "base64", "contentMediaType": "application/octet-stream"}, only31)
    schema("tuple-prefix-items", {"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}], "items": False}, only31)
    schema("array-items-omitted", {"type": "array"}, only31)
    schema("array-items-true", {"type": "array", "items": True}, only31)
    schema("array-contains", {"type": "array", "items": FREE, "contains": {"const": {}}, "minContains": 1, "maxContains": 2}, only31)
    schema("array-unevaluated-items", {"type": "array", "prefixItems": [FREE], "unevaluatedItems": False}, only31)

    # A structure with a free-form member already translates under 3.1. Adding
    # one keyword at a time exposes loss without the scalar failure masking it.
    object_keywords = {
        "if-then-else": {"if": {"required": ["payload"]}, "then": {"required": ["other"]}, "else": {"required": ["fallback"]}},
        "dependent-required": {"dependentRequired": {"payload": ["other"]}},
        "dependent-schemas": {"dependentSchemas": {"payload": {"required": ["other"]}}},
        "unevaluated-properties": {"unevaluatedProperties": False},
        "pattern-properties": {"patternProperties": {"^x-": {"type": "integer"}}},
        "property-names": {"propertyNames": {"pattern": "^[a-z]+$"}},
        "schema-dialect": {"$schema": "https://json-schema.org/draft/2020-12/schema"},
        "schema-dialect-draft7": {"$schema": "http://json-schema.org/draft-07/schema#"},
        "arbitrary-keyword": {"customAnnotation": "retained?"},
    }
    for name, keywords in object_keywords.items():
        schema(name, {**STRUCT, **keywords}, only31, "object-properties")
    schema("not", {**STRUCT, "not": {"required": ["payload"]}}, control="object-properties")
    schema("object-size", {**STRUCT, "minProperties": 1, "maxProperties": 2}, control="object-properties")
    schema("object-default", {**STRUCT, "default": {"payload": {}}}, control="object-properties")
    schema("legacy-nullable", {**STRUCT, "nullable": True}, ("3.0.3",), "object-properties")

    # Schema references use their own semantics, distinct from Reference Objects.
    add("schema-ref-description", document({"Base": STRUCT, "Value": {"type": "object", "properties": {"base": {"$ref": "#/components/schemas/Base", "description": "Override description"}}}}), only31, "local-schema-ref")
    add("schema-ref-constraint", document({"Base": STRUCT, "Value": {"type": "object", "properties": {"base": {"$ref": "#/components/schemas/Base", "required": ["payload"]}}}}), only31, "local-schema-ref")
    schema("defs-pointer", {"$defs": {"Inner": STRUCT}, "$ref": "#/components/schemas/Value/$defs/Inner"}, only31)
    schema("anchor-ref", {"$defs": {"Inner": {"$anchor": "inner", **STRUCT}}, "$ref": "#inner"}, only31)
    schema("id-relative-ref", {"$id": "https://example.invalid/schemas/root", "$defs": {"Inner": {"$id": "inner", **STRUCT}}, "$ref": "inner"}, only31)
    add("dynamic-ref", document({"Base": {"$dynamicAnchor": "node", **STRUCT}, "Value": {"type": "object", "properties": {"payload": {"$dynamicRef": "#node"}}}}), only31)
    schema("defs-unused", {**STRUCT, "$defs": {"Unused": {"type": "string"}}}, only31, "object-properties")
    schema("defs-member-ref", {"type": "object", "$defs": {"Inner": STRUCT}, "properties": {"inner": {"$ref": "#/components/schemas/Value/$defs/Inner"}}}, only31)
    add("anchor-matches-name", document({"Base": {"$anchor": "base", **STRUCT}, "Value": {"type": "object", "properties": {"base": {"$ref": "#base"}}}}), only31)
    add("anchor-component-ref", document({"Base": {"$anchor": "lookup", **STRUCT}, "Value": {"type": "object", "properties": {"base": {"$ref": "#lookup"}}}}), only31)

    endpoint = document()
    endpoint["paths"] = {"/value": {"get": operation()}}
    add("http-operation", endpoint)
    schema_only = document({"Value": STRUCT})
    del schema_only["paths"]
    add("components-without-paths", schema_only, only31, "object-properties")
    webhooks = copy.deepcopy(endpoint)
    webhooks["webhooks"] = {"valueChanged": {"post": operation()}}
    webhooks["webhooks"]["valueChanged"]["post"]["operationId"] = "onValueChanged"
    add("webhooks-with-paths", webhooks, only31, "http-operation")
    hooks_only = document()
    del hooks_only["paths"]
    hooks_only["webhooks"] = webhooks["webhooks"]
    add("webhooks-only", hooks_only, only31)
    path_ref = document()
    path_ref["paths"] = {"/value": {"$ref": "#/components/pathItems/ValuePath"}}
    path_ref["components"] = {"pathItems": {"ValuePath": {"get": operation()}}}
    add("components-path-items", path_ref, only31)
    no_responses = document()
    no_responses["paths"] = {"/value": {"get": {"operationId": "getValue"}}}
    add("operation-without-responses", no_responses, only31)
    for method in ("get", "head", "delete"):
        body = document()
        body["paths"] = {"/value": {method: {**operation(), "requestBody": {"required": True, "content": {"application/json": {"schema": FREE}}}}}}
        add(method + "-request-body", body, only31)
    raw_body = copy.deepcopy(endpoint)
    raw_body["paths"]["/value"]["get"]["responses"]["200"]["content"] = {"application/octet-stream": {}}
    add("raw-binary-response", raw_body, only31)
    raw_input = copy.deepcopy(endpoint)
    raw_input["paths"]["/value"] = {"post": {**operation(), "requestBody": {"required": True, "content": {"application/octet-stream": {}}}}}
    add("raw-binary-request", raw_input, only31)
    mtls = copy.deepcopy(endpoint)
    mtls["components"] = {"securitySchemes": {"tls": {"type": "mutualTLS"}}}
    mtls["security"] = [{"tls": []}]
    add("mutual-tls", mtls, only31)
    metadata = {
        "info-summary": ("info", {"summary": "Service summary"}),
        "license-identifier": ("info", {"license": {"name": "Apache 2.0", "identifier": "Apache-2.0"}}),
    }
    for name, (key, value) in metadata.items():
        doc = copy.deepcopy(endpoint)
        doc[key].update(value)
        add(name, doc, only31, "http-operation")
    dialect = copy.deepcopy(endpoint)
    dialect["jsonSchemaDialect"] = "https://json-schema.org/draft/2020-12/schema"
    add("root-schema-dialect", dialect, only31, "http-operation")
    response_ref = copy.deepcopy(endpoint)
    response = response_ref["paths"]["/value"]["get"]["responses"]["200"]
    response_ref["components"] = {"responses": {"Result": response}}
    response_ref["paths"]["/value"]["get"]["responses"]["200"] = {"$ref": "#/components/responses/Result"}
    add("response-ref", response_ref)
    ref_meta = copy.deepcopy(response_ref)
    ref_meta["paths"]["/value"]["get"]["responses"]["200"].update({"summary": "Override summary", "description": "Override description"})
    add("response-ref-metadata", ref_meta, only31, "response-ref")
    return result


def read_classpath(path):
    text = path.read_text().strip()
    if text.startswith("["):
        return os.pathsep.join(item.split(":", 3)[-1] for item in json.loads(text))
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classpath-file", type=Path, required=True, help="Mill show runClasspath JSON or a plain JVM classpath")
    parser.add_argument("--output", type=Path, default=ROOT / ".cache/openapi-3.1/inventory")
    parser.add_argument("--filter", default="", help="Only run case names containing this text")
    parser.add_argument("--assert-scalars", action="store_true", help="Fail unless scalar types and issue #306's member targets are correct")
    args = parser.parse_args()
    base = args.output.resolve()
    inputs, outputs, classes = (base / name for name in ("inputs", "results", "classes"))
    for folder in (inputs, outputs, classes):
        folder.mkdir(parents=True, exist_ok=True)
    selected = [case for case in cases() if args.filter in case["name"]]
    if not selected:
        parser.error("No matching cases")
    for case in selected:
        case["file"] = case["name"] + "--" + case["version"] + ".json"
        (inputs / case["file"]).write_text(json.dumps(case["document"], indent=2) + "\n")
    (base / "manifest.json").write_text(json.dumps(selected, indent=2) + "\n")
    cp = read_classpath(args.classpath_file)
    jdk = Path(os.environ["JAVA_HOME"]) / "bin" if "JAVA_HOME" in os.environ else Path()
    subprocess.run([str(jdk / "javac"), "-cp", cp, "-d", str(classes), str(HERE / "Probe.java")], check=True)
    start = time.monotonic()
    with (base / "probe.log").open("w") as log:
        subprocess.run([str(jdk / "java"), "-cp", str(classes) + os.pathsep + cp, "Probe", str(inputs), str(outputs), *[case["file"] for case in selected]], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
    results = {case["file"]: json.loads((outputs / case["file"]).read_text()) for case in selected}
    rows = []
    assertions = []
    for case in selected:
        result = results[case["file"]]
        compiled = result["compile"]
        shapes = compiled.get("model", {}).get("shapes", {})
        errors = compiled.get("errors", [])
        control = results.get(str(case["control"]) + "--" + case["version"] + ".json")
        identical = control is not None and shapes == control["compile"].get("model", {}).get("shapes", {})
        if result.get("parserMessages"):
            outcome = "input rejected"
        elif compiled["status"] == "exception":
            outcome = "exception"
        elif any("$Restriction:" in error for error in errors):
            outcome = "unsupported schema/feature"
        elif errors or compiled["status"] == "failure":
            outcome = "model validation failure"
        else:
            outcome = "no diagnostics (inspect semantics)"
        rows.append({
            "case": case["name"], "version": case["version"],
            "outcome": outcome,
            "parser_messages": len(result.get("parserMessages") or []),
            "status": compiled["status"], "errors": len(errors),
            "validated_status": result["validatedCompile"]["status"],
            "identical_to_control": str(identical) if control is not None else "",
            "shapes": ", ".join(name + ":" + shape["type"] for name, shape in shapes.items()),
        })
        if case["name"].startswith("scalar-"):
            kind = case["name"].removeprefix("scalar-")
            expected = "double" if kind == "number" else kind
            assertions.append((case["file"], not errors and shapes.get("openapi#Value", {}).get("type") == expected))
        if case["name"] == "issue-306":
            for shape, member, target in [("GetItemInput", "id", "String"), ("Body", "name", "String"), ("Body", "count", "Integer"), ("Body", "active", "Boolean")]:
                actual = shapes.get("openapi#" + shape, {}).get("members", {}).get(member, {}).get("target")
                assertions.append((case["file"] + " " + member, actual == "smithy.api#" + target))
    with (base / "matrix.tsv").open("w") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Ran {len(selected)} documents in {time.monotonic() - start:.1f}s. Results: {base}")
    for row in rows:
        detail = f"{row['status']}, {row['errors']} errors; {row['shapes']}"
        if row["identical_to_control"] == "True":
            detail += "; IDENTICAL TO CONTROL"
        print(f"{row['case']} ({row['version']}): {detail}")
    if args.assert_scalars:
        if not assertions:
            parser.error("--assert-scalars requires scalar or issue-306 cases")
        for name, passed in assertions:
            print(f"{'PASS' if passed else 'FAIL'} {name}")
        return int(any(not passed for _, passed in assertions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
