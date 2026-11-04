use crate::{
    config::{Settings, is_blank},
    error::ServiceError,
};
use serde::{
    Deserialize, Deserializer, Serialize,
    de::{self, MapAccess, SeqAccess, Visitor},
};
use serde_json::{Map, Value};
use std::{collections::HashSet, fmt};

pub(crate) const NONE: &str = "__none__";
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Category {
    pub id: String,
    pub description: String,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ClassifyRequest {
    pub text: String,
    pub options: Vec<Category>,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Probability {
    pub id: String,
    pub probability: f64,
}
#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Status {
    Classified,
    NoMatch,
    NeedsReview,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ClassifyResponse {
    pub status: Status,
    pub label: Option<String>,
    pub candidate: String,
    pub distribution: Vec<Probability>,
    pub provider_confidence: Option<f64>,
    pub backend: String,
    pub configured_model: Option<String>,
    pub reported_model: Option<String>,
}

// serde_json::Value normally silently overwrites duplicate object keys. Walk every
// object (including unknown provider fields) before interpreting either boundary.
struct Unique(Value);
impl<'de> Deserialize<'de> for Unique {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct JsonVisitor;
        impl<'de> Visitor<'de> for JsonVisitor {
            type Value = Unique;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("JSON without duplicate keys")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> Result<Unique, E> {
                serde_json::Number::from_f64(v)
                    .map(|n| Unique(Value::Number(n)))
                    .ok_or_else(|| E::custom("nonfinite number"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_unit<E: de::Error>(self) -> Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Unique, A::Error> {
                let mut values = Vec::new();
                while let Some(Unique(v)) = seq.next_element()? {
                    values.push(v);
                }
                Ok(Unique(Value::Array(values)))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Unique, A::Error> {
                let mut values = Map::new();
                while let Some(key) = map.next_key::<String>()? {
                    if values.contains_key(&key) {
                        return Err(de::Error::custom("duplicate key"));
                    }
                    let Unique(value) = map.next_value()?;
                    values.insert(key, value);
                }
                Ok(Unique(Value::Object(values)))
            }
        }
        d.deserialize_any(JsonVisitor)
    }
}
pub(crate) fn decode(body: &[u8]) -> Result<Value, serde_json::Error> {
    serde_json::from_slice::<Unique>(body).map(|v| v.0)
}
pub(crate) fn parse(body: &[u8], s: &Settings) -> Result<ClassifyRequest, ServiceError> {
    use ServiceError::*;
    let request: ClassifyRequest =
        serde_json::from_value(decode(body).map_err(|_| InvalidRequest)?)
            .map_err(|_| InvalidRequest)?;
    if is_blank(&request.text) || request.text.len() > s.max_text {
        return Err(InvalidText);
    }
    if !(2..=s.max_categories).contains(&request.options.len()) {
        return Err(InvalidCategoryCount);
    }
    let mut ids = HashSet::new();
    for category in &request.options {
        let id = category.id.as_bytes();
        if id.is_empty()
            || id.len() > 64
            || !id[0].is_ascii_lowercase()
            || !id
                .iter()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, b'_' | b'-'))
            || !ids.insert(&category.id)
        {
            return Err(InvalidCategoryId);
        }
    }
    if request
        .options
        .iter()
        .any(|c| is_blank(&c.description) || c.description.len() > s.max_description)
    {
        return Err(InvalidCategoryDescription);
    }
    Ok(request)
}
pub(crate) fn interpret(
    payload: Value,
    request: &ClassifyRequest,
    model: &str,
) -> Result<ClassifyResponse, ServiceError> {
    let invalid = ServiceError::BackendInvalidOutput;
    let answers = payload
        .get("answers")
        .and_then(Value::as_object)
        .ok_or(invalid)?;
    if answers.len() != 1 {
        return Err(invalid);
    }
    let answer = answers
        .get("classification")
        .and_then(Value::as_object)
        .ok_or(invalid)?;
    if answer.get("type").and_then(Value::as_str) != Some("choice") {
        return Err(invalid);
    }
    let probabilities = answer
        .get("probabilities")
        .and_then(Value::as_object)
        .ok_or(invalid)?;
    let ids: Vec<&str> = request
        .options
        .iter()
        .map(|c| c.id.as_str())
        .chain(std::iter::once(NONE))
        .collect();
    if probabilities.len() != ids.len() {
        return Err(invalid);
    }
    let distribution: Vec<Probability> = ids
        .iter()
        .map(|id| {
            let p = probabilities
                .get(*id)
                .and_then(Value::as_f64)
                .filter(|p| p.is_finite() && (0.0..=1.0).contains(p))
                .ok_or(invalid)?;
            Ok(Probability {
                id: (*id).into(),
                probability: p,
            })
        })
        .collect::<Result<_, ServiceError>>()?;
    // Compensated summation preserves the rounding boundary for many categories.
    let (mut total, mut correction) = (0.0, 0.0);
    for p in &distribution {
        let y = p.probability - correction;
        let t = total + y;
        correction = (t - total) - y;
        total = t;
    }
    if total <= 0.0 || (total - 1.0).abs() > ids.len() as f64 * 0.005 + 1e-12 {
        return Err(invalid);
    }
    let maximum = distribution
        .iter()
        .map(|p| p.probability)
        .fold(0.0, f64::max);
    let winners: Vec<&str> = distribution
        .iter()
        .filter(|p| p.probability == maximum)
        .map(|p| p.id.as_str())
        .collect();
    let choice = answer
        .get("choice")
        .and_then(Value::as_str)
        .ok_or(invalid)?;
    if !winners.contains(&choice) {
        return Err(invalid);
    }
    let candidate = winners.first().ok_or(invalid)?.to_string();
    let status = if winners.len() > 1 {
        Status::NeedsReview
    } else if candidate == NONE {
        Status::NoMatch
    } else {
        Status::Classified
    };
    let confidence = answer.get("confidence").and_then(Value::as_f64);
    if confidence.is_some_and(|p| !p.is_finite() || !(0.0..=1.0).contains(&p)) {
        return Err(invalid);
    }
    let reported = match payload.get("model") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) if !is_blank(s) => Some(s.clone()),
        _ => return Err(invalid),
    };
    Ok(ClassifyResponse {
        label: (status == Status::Classified).then(|| candidate.clone()),
        status,
        candidate,
        distribution,
        provider_confidence: confidence,
        backend: "jev".into(),
        configured_model: Some(model.into()),
        reported_model: reported,
    })
}
