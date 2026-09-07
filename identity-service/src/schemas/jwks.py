def jwks_response(km) -> dict:
    return {"keys": [k.public_jwk for k in km.current_keys()]}
