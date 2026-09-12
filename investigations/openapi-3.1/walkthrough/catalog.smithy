$version: "2.0"

namespace catalog

use smithytranslate#contentType

service CatalogService {
    operations: [
        GetItem
    ]
}

@http(
    method: "GET"
    uri: "/items"
    code: 200
)
operation GetItem {
    input: GetItemInput
    output: GetItem200
}

structure GetItem200 {
    @httpPayload
    @required
    @contentType("application/json")
    body: Item
}

structure GetItemInput {
    @httpQuery("id")
    @required
    id: String
}

structure Item {
    @required
    name: String
    count: Integer
}
