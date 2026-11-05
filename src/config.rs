use std::{fmt, time::Duration};

/// Validated startup settings. Credentials are deliberately omitted from Debug.
pub(crate) struct Settings {
    pub base_url: String,
    pub api_key: String,
    pub backend_key: String,
    pub model: String,
    pub timeout: Duration,
    pub max_request: usize,
    pub max_text: usize,
    pub max_categories: usize,
    pub max_description: usize,
    pub concurrency: usize,
}

#[derive(Debug)]
pub(crate) struct ConfigError(pub &'static str);
impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Invalid configuration: {}", self.0)
    }
}
impl std::error::Error for ConfigError {}

pub(crate) fn valid_key(key: &str) -> bool {
    !key.is_empty() && key.bytes().all(|b| (33..=126).contains(&b))
}
// Treat ASCII separators as blank alongside Unicode whitespace so that
// control-only text cannot satisfy the nonblank input requirement.
pub(crate) fn is_blank(value: &str) -> bool {
    value
        .chars()
        .all(|c| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
}
pub(crate) fn valid_url(raw: &str) -> bool {
    let Some((_, rest)) = raw.split_once("://") else {
        return false;
    };
    let authority = rest.split('/').next().unwrap_or("");
    !authority.is_empty()
        && !authority.contains('@')
        && !raw.chars().any(|c| c.is_whitespace() || c.is_control())
        && !raw.contains(['?', '#', '\\'])
        && !raw.split('/').any(|s| matches!(s, "." | ".."))
        && reqwest::Url::parse(raw).is_ok_and(|u| {
            matches!(u.scheme(), "http" | "https")
                && u.host_str().is_some()
                && u.username().is_empty()
                && u.password().is_none()
                && u.port() != Some(0)
        })
}
impl Settings {
    pub fn from_env() -> Result<Self, ConfigError> {
        Self::load(|key| std::env::var(key).ok())
    }
    pub fn load(get: impl Fn(&str) -> Option<String>) -> Result<Self, ConfigError> {
        let required = |name: &'static str| get(name).ok_or(ConfigError(name));
        let number = |name: &'static str, default: usize| -> Result<usize, ConfigError> {
            let Some(raw) = get(name) else {
                return Ok(default);
            };
            if raw.is_empty() || !raw.bytes().all(|c| c.is_ascii_digit()) {
                return Err(ConfigError(name));
            }
            raw.parse().ok().filter(|n| *n > 0).ok_or(ConfigError(name))
        };
        if required("TAGGER_BACKEND")? != "jev" {
            return Err(ConfigError("TAGGER_BACKEND"));
        }
        let base_url = required("TAGGER_BACKEND_BASE_URL")?;
        if !valid_url(&base_url) {
            return Err(ConfigError("TAGGER_BACKEND_BASE_URL"));
        }
        let api_key = required("TAGGER_API_KEY")?;
        let backend_key = required("TAGGER_BACKEND_API_KEY")?;
        if !valid_key(&api_key) || !valid_key(&backend_key) || api_key == backend_key {
            return Err(ConfigError("independent printable ASCII API keys required"));
        }
        let model = required("TAGGER_BACKEND_MODEL")?;
        if is_blank(&model) || model.chars().any(|c| (c as u32) < 32) {
            return Err(ConfigError("TAGGER_BACKEND_MODEL"));
        }
        let seconds = get("TAGGER_REQUEST_TIMEOUT_SECONDS")
            .unwrap_or_else(|| "30".into())
            .trim()
            .parse::<f64>()
            .map_err(|_| ConfigError("TAGGER_REQUEST_TIMEOUT_SECONDS"))?;
        let timeout = Duration::try_from_secs_f64(seconds)
            .ok()
            .filter(|d| !d.is_zero())
            .ok_or(ConfigError("TAGGER_REQUEST_TIMEOUT_SECONDS"))?;
        if tokio::time::Instant::now().checked_add(timeout).is_none() {
            return Err(ConfigError("TAGGER_REQUEST_TIMEOUT_SECONDS"));
        }
        let s = Self {
            base_url: base_url.trim_end_matches('/').into(),
            api_key,
            backend_key,
            model,
            timeout,
            max_request: number("TAGGER_MAX_REQUEST_BYTES", 1_048_576)?,
            max_text: number("TAGGER_MAX_TEXT_BYTES", 65_536)?,
            max_categories: number("TAGGER_MAX_CATEGORIES", 19)?,
            max_description: number("TAGGER_MAX_CATEGORY_DESCRIPTION_BYTES", 2048)?,
            concurrency: number("TAGGER_MAX_CONCURRENT_REQUESTS", 1)?,
        };
        if !(2..=254).contains(&s.max_categories)
            || s.max_request > 32 * 1024 * 1024
            || s.max_text > s.max_request
            || s.max_description > s.max_request
            || s.concurrency > tokio::sync::Semaphore::MAX_PERMITS
        {
            return Err(ConfigError("backend requirements/limits"));
        }
        Ok(s)
    }
}
