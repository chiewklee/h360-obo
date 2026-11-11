%dw 2.0
import fromBase64 from dw::core::Binaries
import repeat from dw::core::Strings

/** First line of Salesforce's first error message in an H360 tool result ({status_code, body}), or null. */
fun sfError(result) = do {
    var body = result.body
    var errs = if ((body is String) and (body startsWith "[")) read(body, "application/json") else body
    ---
    if ((errs is Array) and !isEmpty(errs)) ((errs[0].message default "") splitBy "\n")[0] else null
}

/** Decodes one base64url JWT segment to JSON. */
fun b64urlJson(segment) = do {
    var b64 = (segment default "") replace "-" with "+" replace "_" with "/"
    var pad = if ((sizeOf(b64) mod 4) == 0) 0 else 4 - (sizeOf(b64) mod 4)
    ---
    read(fromBase64(b64 ++ repeat("=", pad)), "application/json")
}

/** Header and claims of a JWT, without verifying it and without the signature. */
fun jwtDecode(token) = do {
    var parts = (token default "") splitBy "."
    ---
    if (sizeOf(parts) == 3) { header: b64urlJson(parts[0]), claims: b64urlJson(parts[1]) } else null
}
