use actix_web::{web, error::InternalError, FromRequest, HttpRequest, HttpResponse};
use std::future::{ready, Ready};
use subtle::ConstantTimeEq;

/// Extracts and validates the `X-Api-Key` header (actix-web extractor).
pub struct ApiKey(#[allow(dead_code)] pub String);

impl FromRequest for ApiKey {
    type Error = actix_web::Error;
    type Future = Ready<Result<Self, Self::Error>>;

    fn from_request(req: &HttpRequest, _: &mut actix_web::dev::Payload) -> Self::Future {
        let config = req
            .app_data::<web::Data<crate::config::Config>>()
            .expect("Config not in app_data");

        match req.headers().get("X-Api-Key").and_then(|v| v.to_str().ok()) {
            // Constant-time comparison to avoid a timing side channel on the key.
            // (Length may differ and short-circuits; the key length is not secret.)
            Some(val)
                if bool::from(val.as_bytes().ct_eq(config.app.key.as_bytes())) =>
            {
                ready(Ok(ApiKey(val.into())))
            }
            _ => ready(Err(InternalError::from_response(
                "Invalid API key",
                HttpResponse::Unauthorized()
                    .json(crate::models::ApiResponse::error(401, "Invalid API key")),
            )
            .into())),
        }
    }
}
