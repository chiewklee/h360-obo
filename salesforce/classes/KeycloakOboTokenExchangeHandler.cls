/**
 * OAuth 2.0 Token Exchange (RFC 8693) handler for the H360 OBO demo.
 *
 * Accepts a user access token from one of two identity providers, chosen by its issuer:
 *   Keycloak (realm h360-obo)  -> Salesforce user whose Username = the `sf_username` claim
 *   Entra ID (tenant e81fdb49) -> Salesforce user whose Federation ID = the `oid` claim
 * In both cases the token is validated against the provider's JWKS (signature + expiry), then
 * audience and authorized party are checked. The token Salesforce issues runs as that person.
 */
global class KeycloakOboTokenExchangeHandler extends Auth.Oauth2TokenExchangeHandler {

    private class Idp {
        String name, issuer, jwks, audience, authorizedParty, scope, userClaim, userField;
        Idp(String name, String issuer, String jwks, String audience, String authorizedParty,
            String scope, String userClaim, String userField) {
            this.name = name; this.issuer = issuer; this.jwks = jwks; this.audience = audience;
            this.authorizedParty = authorizedParty; this.scope = scope;
            this.userClaim = userClaim; this.userField = userField;
        }
    }

    private static final String KEYCLOAK_ISSUER = 'https://keycloak-1792182140.web.cloud-services.demos.mulesoft.com/realms/h360-obo';
    private static final String ENTRA_TENANT = 'e81fdb49-f453-4fb1-acec-589f688c68d6';
    private static final String ENTRA_APP = '618c685b-60ab-4dd2-a825-147941207fde';

    private static final List<Idp> IDPS = new List<Idp>{
        new Idp('Keycloak', KEYCLOAK_ISSUER, KEYCLOAK_ISSUER + '/protocol/openid-connect/certs',
                'h360-mcp', 'h360-mcp-client', null, 'sf_username', 'Username'),
        // scp is required so app-only (client credentials) tokens, which carry no user, are refused
        new Idp('Entra', 'https://login.microsoftonline.com/' + ENTRA_TENANT + '/v2.0',
                'https://login.microsoftonline.com/' + ENTRA_TENANT + '/discovery/v2.0/keys',
                ENTRA_APP, ENTRA_APP, 'access_as_user', 'oid', 'FederationIdentifier')
    };

    global override Auth.TokenValidationResult validateIncomingToken(
            String appDeveloperName, Auth.IntegratingAppType appType,
            String incomingToken, Auth.OAuth2TokenExchangeType tokenType) {
        try {
            Map<String, Object> claims = payload(incomingToken);
            Idp idp = idpFor((String) claims.get('iss'));
            if (idp == null) {
                return invalid('unexpected issuer');
            }
            // Signature (via the provider's JWKS) and exp; throws if either fails.
            Auth.JWTUtil.validateJWTWithKeysEndpoint(incomingToken, idp.jwks, true);

            if (!audiences(claims.get('aud')).contains(idp.audience)) {
                return invalid('token not issued for audience ' + idp.audience);
            }
            if (claims.get('azp') != idp.authorizedParty) {
                return invalid('unexpected client');
            }
            if (idp.scope != null
                    && !String.valueOf(claims.get('scp') ?? '').split(' ').contains(idp.scope)) {
                return invalid('missing delegated scope ' + idp.scope);
            }
            String match = (String) claims.get(idp.userClaim);
            if (String.isBlank(match)) {
                return invalid('missing ' + idp.userClaim + ' claim');
            }

            Auth.UserData userData = new Auth.UserData(
                (String) claims.get('sub'), (String) claims.get('given_name'),
                (String) claims.get('family_name'), (String) claims.get('name'),
                (String) claims.get('email'), null, (String) claims.get('preferred_username'),
                null, idp.name, null,
                new Map<String, String>{ 'match_field' => idp.userField, 'match_value' => match });
            return new Auth.TokenValidationResult(true, null, userData, incomingToken, tokenType, null);
        } catch (Exception e) {
            return invalid('token validation failed: ' + e.getMessage());
        }
    }

    global override User getUserForTokenSubject(
            Id networkId, Auth.TokenValidationResult result, Boolean canCreateUser,
            String appDeveloperName, Auth.IntegratingAppType appType) {
        Map<String, String> attrs = result.getUserData().attributeMap;
        String value = attrs.get('match_value');
        // Salesforce refuses to issue a token for an inactive user, so no IsActive filter here.
        List<User> users = attrs.get('match_field') == 'FederationIdentifier'
            ? [SELECT Id FROM User WHERE FederationIdentifier = :value LIMIT 1]
            : [SELECT Id FROM User WHERE Username = :value LIMIT 1];
        return users.isEmpty() ? null : users[0];
    }

    private static Idp idpFor(String issuer) {
        for (Idp idp : IDPS) {
            if (idp.issuer == issuer) {
                return idp;
            }
        }
        return null;
    }

    private static Auth.TokenValidationResult invalid(String reason) {
        return new Auth.TokenValidationResult(false, null, null, null, null, reason);
    }

    private static Map<String, Object> payload(String jwt) {
        String part = jwt.split('\\.')[1].replace('-', '+').replace('_', '/');
        while (Math.mod(part.length(), 4) != 0) {
            part += '=';
        }
        return (Map<String, Object>) JSON.deserializeUntyped(EncodingUtil.base64Decode(part).toString());
    }

    // aud is a string or an array of strings.
    private static Set<String> audiences(Object aud) {
        Set<String> result = new Set<String>();
        if (aud instanceof String) {
            result.add((String) aud);
        } else if (aud instanceof List<Object>) {
            for (Object a : (List<Object>) aud) {
                result.add(String.valueOf(a));
            }
        }
        return result;
    }
}
