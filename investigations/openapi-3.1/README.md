OpenAPI 3.1 investigation — issue #306
====================================

OpenAPI 3.1 already reaches the converter, but the converter does not understand
Swagger Parser's 3.1 schema representation. This breaks ordinary scalar types
before any new JSON Schema feature is needed. There are also independent parser,
translation, and Smithy validation problems. Several cases silently lose information.

Investigated on 2026-09-10 at upstream `main`, commit
`ff6d089f3c2e5348608c4aa8ac80b08f27b2c061`. The existing checkout at
`~/dev/smithy-translate` matched upstream. The maintainer welcomes a fix in the
[linked issue comment](https://github.com/disneystreaming/smithy-translate/issues/306#issuecomment-5587500651).

Evidence: **152 existing OpenAPI tests passed**; **157 generated documents** were
run against the locally compiled OpenAPI module, including **106 OpenAPI 3.1
documents**. All 106 passed independent checks of OpenAPI document structure and
JSON Schema syntax. This is a representative feature inventory, not a complete
conformance suite or a check of every reference, dialect, and keyword interaction.

The pinned dependencies are Swagger Parser **2.1.19**, Swagger Models **2.2.19**,
Smithy **1.72.0**, and Scala **2.13.18**. The investigation harness runs on Java 17.
The issue's exact CLI example was also reproduced with published version **0.7.8**.
Implementation and existing tests are unchanged; the added files are investigation tools.

**The immediate cause is confirmed.** A minimal document with a single
`components.schemas.Value: {type: string}` produces these parser objects:

| OpenAPI version | Java class | `getType()` | `getTypes()` | Generated shape |
| --- | --- | --- | --- | --- |
| 3.0.3 | `StringSchema` | `string` | `[string]` | `string Value` |
| 3.1.0 | `JsonSchema` | `null` | `[string]` | `error#Value` |

Neither input produces parser diagnostics. The class difference is deliberate:
Swagger documents its [3.1 model representation](https://github.com/swagger-api/swagger-core/wiki/Swagger-2.X---OpenAPI-3.1#representation---models).
The converter's [extractors](../../modules/openapi/src/internals/Extractors.scala)
match `StringSchema`, `IntegerSchema`, `BooleanSchema`, `MapSchema`, and
`ComposedSchema`. Its [schema conversion](../../modules/openapi/src/internals/OpenApiToIModel.scala)
also matches `ArraySchema` directly. `JsonSchema` misses these branches.

Two controlled experiments used the public `ParsedSpec` input with the same
minimal 3.1 document. Setting its schema's `type` to `string` still produced an
error. Replacing that schema with `StringSchema` produced the correct string and
no errors. Thus a `getType()` fallback alone does not address the class matching.
These changes exist only inside the probe.

For the original issue, all four expected member targets fail on both 0.7.8 and
current `main`:

| Member | Expected / 3.0.3 | Actual 3.1.0 |
| --- | --- | --- |
| `GetItemInput$id` | `smithy.api#String` | `error#Id` |
| `Body$name` | `smithy.api#String` | `error#Name` |
| `Body$count` | `smithy.api#Integer` | `error#Count` |
| `Body$active` | `smithy.api#Boolean` | `error#Active` |

The query parameter's invalid target subsequently causes the Smithy `httpQuery`
validation error. With the issue's CLI flags, both runs exit **0**. The compiler
can return `Success(errors, model)`, and the CLI prints diagnostics while writing
that partial model. Consequently, process success and the existence of output
files are insufficient tests. `--validate-output` changes compiler failure
handling; it does not add support for these schemas. See
[AbstractToSmithyCompiler](../../modules/compiler-core/src/AbstractToSmithyCompiler.scala)
and [ReportResult](../../modules/runners/src/openapi/ReportResult.scala).

**Existing capabilities that regress with a 3.1 input.** Case names identify the
generated fixtures; exact results are in [matrix.tsv](matrix.tsv).

| Feature / cases | Observed 3.1 result | Cause or qualification |
| --- | --- | --- |
| `scalar-*`, `format-*` | Error placeholders for string, integer, number, boolean and all tested formats | Class dispatch. Includes date/time, UUID, password, email, integer widths, float/double and local-date. |
| `string-enum` | Error placeholder | `CaseEnum` requires `StringSchema`. |
| `list`, `list-free-items`, `set` | Error placeholder | Requires `ArraySchema`, even with otherwise translatable items. |
| `typed-map`, `typed-map-free-values` | **Becomes `document`, no diagnostics** | `CaseMap` misses `JsonSchema`; `IsFreeForm` then misclassifies it. Value type and constraints disappear. |
| `object-closed` | **Becomes `document`, declared members disappear** | 3.1 represents boolean `additionalProperties` as a schema object. The free-form check does not inspect its boolean value. |
| `object-empty` | Error placeholder | The 3.0 `ObjectSchema` free-form fallback no longer matches. |
| `allOf`, `oneOf` | Error placeholder | Extractors require `ComposedSchema`. |
| `string-constraints`, `integer-range` | Error placeholders | Ordinary length/pattern/range behavior is initially blocked by scalar dispatch. |

**3.1 additions and changed behavior.** OpenAPI 3.1 adopts JSON Schema 2020-12
and changes document-level fields as well as schema syntax. The authoritative
definitions are in the [OpenAPI 3.1 specification](https://spec.openapis.org/oas/v3.1.0.html),
[JSON Schema Core](https://json-schema.org/draft/2020-12/json-schema-core), and
[JSON Schema Validation](https://json-schema.org/draft/2020-12/json-schema-validation).
The outcomes below come from executing this repository.

| Feature / cases | Observed result | Where further work belongs |
| --- | --- | --- |
| `type-singleton-array`, `nullable-string`, `multi-type`, `null-type` | Error placeholders | Interpret type sets, then define null and union mappings. |
| `multi-type-with-properties` | `type: [object, string]` becomes only a structure, without diagnostics | Property detection bypasses the type set. |
| `nullable-object` | Same structure as its non-nullable control | Acceptance does not establish correct null semantics. Requiredness, null, and omission need separate decisions. |
| `empty-schema` | `{}` produces an error placeholder | Unconstrained schemas need explicit handling. |
| `boolean-true`, `boolean-false` | Parser rejects boolean component schemas as non-objects | A pinned-parser limitation. Disabling input validation drops the component entirely. |
| `boolean-property`, `boolean-property-true` | Parser accepts nested booleans; converter emits error placeholders | Separate converter handling is also needed. |
| `const-string`, `const-only`, `const-object` | String forms fail; object `const` disappears with no diagnostic | A real constraint gap beyond scalar dispatch. |
| `numeric-exclusive-bounds` | Scalar error; independent experiment also loses numeric bounds | Parser fills `getExclusiveMinimumValue` / `getExclusiveMaximumValue`. Converter reads the old boolean getters. Even a recognized `IntegerSchema` carrying the numeric fields emits an unconstrained integer. |
| `schema-examples` | Examples disappear; singular `schema-example` is retained | `getExamplesHint` only reads `getExample()`. A small independent fix. |
| `tuple-prefix-items`, `array-items-omitted`, `array-items-true` | Error placeholders | Array dispatch masks the next issue; tuple and unconstrained-item handling have no translation branches. |
| `array-contains`, `array-unevaluated-items` | Error placeholders | Array dispatch plus missing handling of these assertions. |
| `if-then-else`, `dependent-required`, `dependent-schemas` | Same output as a structure without the keywords; no diagnostics | Constraints are not represented. |
| `unevaluated-properties`, `pattern-properties`, `property-names` | Same output as their control; no diagnostics | Object validation semantics are not represented. |
| `schema-ref-description`, `schema-ref-constraint` | `$ref` sibling description and `required` constraint disappear | Reference branch ignores sibling schema keywords. |
| `defs-member-ref` | Unresolved generated Smithy target | `$defs` is preserved as raw extension data, but nested definitions are not compiled. |
| `defs-pointer`, `anchor-ref`, `id-relative-ref` | Uncaught `RuntimeException` during conversion | These root aliases reach `IModelToSmithy.toPrimitive` with unresolved/nonprimitive targets. They combine reference-resolution and alias-handling problems. |
| `anchor-component-ref` | Unresolved `openapi#Lookup` for `$anchor: lookup` on component `Base` | Anchor is treated as a shape name. Matching anchor/component names can succeed accidentally (`anchor-matches-name`). |
| `dynamic-ref` | Error placeholder, `$dynamicRef` copied into extensions | Dynamic reference semantics are not implemented by the converter. |
| `binary-content-encoding` | Error placeholder | Scalar dispatch blocks it; content encoding/media-type fields have no conversion branches. |
| `raw-binary-request`, `raw-binary-response` | `Schema not supported: null` | Schema-less binary media types reach conversion as a null schema through `ContentToSchemaOpt`. |
| `webhooks-with-paths`, `webhooks-only` | Webhooks disappear; webhook-only document produces no operations | `ParseOperations` traverses only `paths`. |
| `components-path-items` | Referenced path item produces no operation, with no diagnostics | Operations are read directly from the Path Item; its `$ref` is not followed. |
| `operation-without-responses` | Parser diagnostic; disabling input validation causes an NPE | Parser still requires `responses`; `ParseOperations.getOutputs` dereferences null. Both layers need attention. |
| `get-request-body`, `head-request-body`, `delete-request-body` | Request body is preserved; output validation fails | Smithy emits `HttpMethodSemantics.UnexpectedPayload` at DANGER severity. This needs a target-model policy, rather than a parser fix. |
| `mutual-tls` | Explicit `MutualTLS is not a supported security scheme` restriction | Deliberately unsupported in `ParseSecuritySchemes`. |
| `response-ref-metadata`, `info-summary`, `license-identifier` | Metadata additions absent from output; no diagnostics | Documentation/metadata preservation work. |

The silent-loss probes add one feature to an already translatable structure
whose member targets an explicit free-form object. This avoids mistaking a
scalar failure for a constraint-specific failure. Their output is compared
with the corresponding control's complete generated shapes.

**Some cases already work.** These have checked model content, not merely a
successful return value:

- Explicit free-form objects using `additionalProperties: true` produce `Document`.
- Structures with translatable members retain those members, `required`, descriptions,
  singular `example` as `alloy#dataExamples`, and `x-*` extension data.
- Ordinary local component-schema and component-response references resolve.
- HTTP operations using those translatable schemas retain their operation and payload bindings.
- Component-only documents without a `paths` field translate.
- The tested arbitrary schema annotation is retained in `alloy.openapi#openapiExtensions`.
  Unused `$defs` data is also retained there; retention alone does not implement its semantics.

**Existing limitations and remaining uncertainty.** `anyOf` is already explicitly
unsupported in 3.0. The probes also find that integer enum restrictions, `not`,
object size limits, object defaults, and composition alongside sibling properties
can already be lost under 3.0. Integer enums have a
[separate existing issue](https://github.com/disneystreaming/smithy-translate/issues/279).
Standalone 3.0 byte/binary schemas expose an existing missing `Blob` case in
`IModelToSmithy.toPrimitive`; these should not be counted as successful 3.0 controls.

`jsonSchemaDialect` and `$schema` are accepted in the tested examples, but those
examples do not distinguish dialect-specific behavior. Dialect support remains
unproven. Custom vocabularies, remote reference retrieval, every schema position,
contentSchema, and all combinations of applicators were not exhaustively tested.
The separate JSON Schema frontend shares compiler internals but is not used by
the OpenAPI frontend, so its supported features cannot be assumed to work here.

Nullability needs particular care: Smithy has its own
[member optionality rules](https://smithy.io/2.0/spec/aggregate-types.html#structure-member-optionality).
A schema that becomes a structure is not sufficient evidence that required
nullable values or null collection elements retain their intended behavior.

**Suggested first PR:** restore scalar type and existing format recognition for
3.1. Test the issue's exact member targets, standalone primitives, and the 3.0
controls through `UnparsedSpecs`, so the tests exercise Swagger deserialization.
Keep type sets containing multiple types explicit until their mapping is decided.
The small scalar reproduction takes about two seconds after compilation.

Subsequent PRs can address string enums; arrays/sets; maps and boolean
`additionalProperties`; `allOf`/`oneOf`; plural examples; numeric exclusive bounds;
then null/type unions, boolean schemas, and the individual reference cases.
Examples can be fixed independently. Parser upgrades, path-item references, and
missing-response handling should each have their own targeted checks. Webhooks,
mutual TLS, advanced validation assertions, and HTTP body validation need a
clear mapping or diagnostic policy before implementation. A scalar fix alone
would leave the silent map and closed-object losses in place.

**Reproduce and extend the investigation.** From the repository root, with JDK
17+ available, prepare the local module's classpath:

```sh
# This machine already has this JDK installed.
export JAVA_HOME="$HOME/.local/share/mise/installs/java/temurin-17"
export MILL_FINAL_DOWNLOAD_FOLDER="$PWD/.cache/mill"
export COURSIER_CACHE="$PWD/.cache/coursier"
mkdir -p .cache/openapi-3.1
./mill --no-server show 'openapi[2.13.18].runClasspath' > .cache/openapi-main-classpath.json
```

Run the exact issue through the local compiler with assertions on its four targets:

```sh
python3 investigations/openapi-3.1/inventory.py \
  --classpath-file .cache/openapi-main-classpath.json \
  --filter issue-306 --assert-scalars \
  --output .cache/openapi-3.1/issue-check
```

Expected on the investigated commit: four PASS lines for 3.0.3, four FAIL lines
for 3.1.0, exit 1. `--filter scalar-string` supplies the minimized case.
Run the full inventory without a filter:

```sh
python3 investigations/openapi-3.1/inventory.py \
  --classpath-file .cache/openapi-main-classpath.json --assert-scalars
```

Full runs took 12–18 seconds inside the JVM, plus harness
compilation. Eight target/type assertions fail. Omitting `--assert-scalars`
collects observations and exits 0; that mode makes **no support verdict**.
Every fixture, parser snapshot, compiler diagnostic, generated model, and
validation result is saved under `.cache/openapi-3.1/inventory/`. `matrix.tsv`
in this directory is a saved snapshot of that run. Its
`no diagnostics (inspect semantics)` outcome must not be interpreted as full support.

To independently check fixture syntax, download the
[official document schema](https://spec.openapis.org/oas/3.1/schema/2025-11-23.html)
and run the optional validator:

```sh
curl --fail --location https://spec.openapis.org/oas/3.1/schema/2025-11-23 \
  --output .cache/openapi-3.1/oas31-schema.json
UV_CACHE_DIR="$PWD/.cache/uv" uv run --with jsonschema==4.25.1 python \
  investigations/openapi-3.1/validate.py \
  .cache/openapi-3.1/inventory/inputs .cache/openapi-3.1/oas31-schema.json
```

This validates document structure and Draft 2020-12 schema syntax, with the
limitations stated above. It does not validate every reference target or switch
semantics for the alternate-dialect probe.

For the original published CLI, with `cs` on PATH:

```sh
cs fetch --classpath com.disneystreaming.smithy:smithytranslate-cli_2.13:0.7.8 \
  > .cache/cli-0.7.8.classpath
python3 investigations/openapi-3.1/reproduce.py \
  --classpath-file .cache/cli-0.7.8.classpath
```

That reproducer asserts targets and retains the CLI logs and output models.
The unchanged baseline suite is `./mill --no-server 'openapi[2.13.18].test'`.
