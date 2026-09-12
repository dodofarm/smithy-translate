// Local investigation harness; deliberately outside the production/test source trees.
import cats.data.NonEmptyList;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.swagger.parser.OpenAPIParser;
import io.swagger.v3.oas.models.media.Schema;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import scala.collection.immutable.List$;
import scala.collection.immutable.Map$;
import scala.collection.immutable.Set$;
import scala.jdk.javaapi.CollectionConverters;
import smithytranslate.compiler.FileContents;
import smithytranslate.compiler.ToSmithyCompilerOptions;
import smithytranslate.compiler.ToSmithyResult;
import smithytranslate.compiler.openapi.OpenApiCompiler$;
import smithytranslate.compiler.openapi.OpenApiCompilerInput;
import software.amazon.smithy.model.Model;
import software.amazon.smithy.model.node.Node;
import software.amazon.smithy.model.shapes.ModelSerializer;

public final class Probe {
    private static final ObjectMapper JSON = new ObjectMapper();

    @SuppressWarnings("rawtypes")
    private static Object schemaInfo(Schema schema, int depth) {
        if (schema == null || depth > 6) return null;
        Map<String, Object> info = new LinkedHashMap<>();
        info.put("class", schema.getClass().getSimpleName());
        info.put("type", schema.getType());
        info.put("types", schema.getTypes());
        // Inspect only synthetic fixture data; no external documents are used.
        for (String getter : List.of("get$ref", "get$id", "get$anchor", "get$schema",
                "get$dynamicRef", "get$dynamicAnchor", "getBooleanSchemaValue",
                "getConst", "getEnum", "getMinimum", "getMaximum",
                "getExclusiveMinimum", "getExclusiveMaximum",
                "getExclusiveMinimumValue", "getExclusiveMaximumValue",
                "getExamples", "getExample", "getDescription", "getDefault",
                "getContentEncoding", "getContentMediaType", "getNullable",
                "getPattern", "getMinLength", "getMinContains", "getMaxContains",
                "getDependentRequired", "getExtensions")) {
            try {
                Object value = schema.getClass().getMethod(getter).invoke(schema);
                if (value != null) info.put(getter.substring(3), value);
            } catch (ReflectiveOperationException ignored) {
                // Some getters do not exist in the pinned Swagger model version.
            }
        }
        if (schema.getProperties() != null) {
            Map<String, Object> properties = new LinkedHashMap<>();
            for (Object key : schema.getProperties().keySet()) {
                properties.put(key.toString(), schemaInfo((Schema) schema.getProperties().get(key), depth + 1));
            }
            info.put("properties", properties);
        }
        if (schema.getItems() != null) info.put("items", schemaInfo(schema.getItems(), depth + 1));
        if (schema.getAdditionalProperties() instanceof Schema) {
            info.put("additionalProperties", schemaInfo((Schema) schema.getAdditionalProperties(), depth + 1));
        } else if (schema.getAdditionalProperties() != null) {
            info.put("additionalProperties", schema.getAdditionalProperties());
        }
        return info;
    }

    private static List<String> errors(scala.collection.immutable.List<?> values) {
        List<String> result = new ArrayList<>();
        for (Object value : CollectionConverters.asJava(values)) result.add(String.valueOf(value));
        return result;
    }

    private static Map<String, Object> compile(String text, boolean validateOutput) {
        return compile(text, validateOutput, true);
    }

    private static Map<String, Object> compile(String text, boolean validateOutput, boolean validateInput) {
        var file = new FileContents(new NonEmptyList<String>("openapi.json", List$.MODULE$.empty()), text);
        var input = new OpenApiCompilerInput.UnparsedSpecs(CollectionConverters.asScala(List.of(file)).toList());
        return compileInput(input, validateOutput, validateInput);
    }

    private static Map<String, Object> compileInput(OpenApiCompilerInput input, boolean validateOutput) {
        return compileInput(input, validateOutput, true);
    }

    private static Map<String, Object> compileInput(OpenApiCompilerInput input, boolean validateOutput, boolean validateInput) {
        Map<String, Object> output = new LinkedHashMap<>();
        try {
            var options = ToSmithyCompilerOptions.apply(false, validateInput, validateOutput,
                List$.MODULE$.empty(), false, true, Set$.MODULE$.empty(), Map$.MODULE$.empty());
            var result = OpenApiCompiler$.MODULE$.compile(options, input);
            if (result instanceof ToSmithyResult.Success) {
                var success = (ToSmithyResult.Success<Model>) result;
                output.put("status", "success");
                output.put("errors", errors(success.error()));
                var serializer = ModelSerializer.builder().shapeFilter(shape ->
                    List.of("openapi", "error").contains(shape.getId().getNamespace())).build();
                output.put("model", JSON.readTree(Node.prettyPrintJson(serializer.serialize(success.value()))));
            } else {
                var failure = (ToSmithyResult.Failure<?>) result;
                output.put("status", "failure");
                output.put("cause", String.valueOf(failure.cause()));
                output.put("errors", errors(failure.errors()));
            }
        } catch (Exception exception) {
            output.put("status", "exception");
            output.put("cause", exception.toString());
            List<String> stack = new ArrayList<>();
            for (StackTraceElement frame : exception.getStackTrace()) {
                if (frame.getClassName().startsWith("smithytranslate")) stack.add(frame.toString());
            }
            output.put("stack", stack);
        }
        return output;
    }

    private static void scalarExperiment(Path input, Path output) throws Exception {
        String text = Files.readString(input);
        Map<String, Object> results = new LinkedHashMap<>();
        results.put("unmodified", compile(text, false));
        var api = new OpenAPIParser().readContents(text, null, null).getOpenAPI();
        var path = new NonEmptyList<String>("openapi.json", List$.MODULE$.empty());
        api.getComponents().getSchemas().get("Value").setType("string");
        results.put("setTypeOnly", compileInput(new OpenApiCompilerInput.ParsedSpec(path, api), false));
        // Only the minimized scalar is replaced. This is a causal probe, not a general adapter.
        api.getComponents().getSchemas().put("Value", new io.swagger.v3.oas.models.media.StringSchema());
        results.put("replaceWithStringSchema", compileInput(new OpenApiCompilerInput.ParsedSpec(path, api), false));
        var integer = new io.swagger.v3.oas.models.media.IntegerSchema();
        integer.setExclusiveMinimumValue(new java.math.BigDecimal("1"));
        integer.setExclusiveMaximumValue(new java.math.BigDecimal("5"));
        api.getComponents().getSchemas().put("Value", integer);
        results.put("numericBoundsOnRecognizedClass", compileInput(new OpenApiCompilerInput.ParsedSpec(path, api), false));
        JSON.writerWithDefaultPrettyPrinter().writeValue(output.toFile(), results);
    }

    public static void main(String[] args) throws Exception {
        if (args[0].equals("--scalar-experiment")) {
            scalarExperiment(Path.of(args[1]), Path.of(args[2]));
            return;
        }
        Path inputs = Path.of(args[0]);
        Path outputs = Path.of(args[1]);
        Files.createDirectories(outputs);
        var selected = java.util.Set.copyOf(java.util.Arrays.asList(args).subList(2, args.length));
        try (var files = Files.list(inputs)) {
            for (Path file : files.filter(path -> path.toString().endsWith(".json"))
                    .filter(path -> selected.isEmpty() || selected.contains(path.getFileName().toString()))
                    .sorted().toList()) {
                String text = Files.readString(file);
                Map<String, Object> output = new LinkedHashMap<>();
                try {
                    var parsed = new OpenAPIParser().readContents(text, null, null);
                    output.put("parserMessages", parsed.getMessages());
                    if (parsed.getOpenAPI() != null) {
                        var api = parsed.getOpenAPI();
                        Map<String, Object> schemas = new LinkedHashMap<>();
                        if (api.getComponents() != null && api.getComponents().getSchemas() != null) {
                            api.getComponents().getSchemas().forEach((name, schema) -> schemas.put(name, schemaInfo(schema, 0)));
                        }
                        output.put("parsedSchemas", schemas);
                        output.put("parsedPaths", api.getPaths() == null ? null : api.getPaths().keySet());
                        output.put("parsedWebhooks", api.getWebhooks() == null ? null : api.getWebhooks().keySet());
                    }
                } catch (Exception exception) {
                    output.put("parserException", exception.toString());
                }
                var result = compile(text, false);
                output.put("compile", result);
                output.put("validatedCompile", compile(text, true));
                if (output.get("parserMessages") instanceof List
                        && !((List<?>) output.get("parserMessages")).isEmpty()) {
                    output.put("withoutInputValidation", compile(text, false, false));
                }
                JSON.writerWithDefaultPrettyPrinter().writeValue(outputs.resolve(file.getFileName()).toFile(), output);
                System.out.println(file.getFileName() + ": " + result.get("status")
                    + "; errors=" + ((List<?>) result.getOrDefault("errors", List.of())).size());
            }
        }
    }
}
