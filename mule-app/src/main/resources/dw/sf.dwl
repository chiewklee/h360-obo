%dw 2.0

/** First line of Salesforce's first error message in an H360 tool result ({status_code, body}), or null. */
fun sfError(result) = do {
    var body = result.body
    var errs = if ((body is String) and (body startsWith "[")) read(body, "application/json") else body
    ---
    if ((errs is Array) and !isEmpty(errs)) ((errs[0].message default "") splitBy "\n")[0] else null
}
