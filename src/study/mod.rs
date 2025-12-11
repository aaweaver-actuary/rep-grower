use reqwest::StatusCode;
use reqwest::blocking::Client;
use serde::Deserialize;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Deserialize)]
pub struct StudyConfig {
    pub token: String,
    pub study_id: String,
    #[serde(default = "default_base_url")]
    pub base_url: String,
    pub default_orientation: Option<String>,
}

fn default_base_url() -> String {
    "https://lichess.org".to_string()
}

impl StudyConfig {
    pub fn from_path(path: impl AsRef<Path>) -> Result<Self, StudyError> {
        let text = fs::read_to_string(path)?;
        let parsed: StudyConfig = toml::from_str(&text)?;
        if parsed.token.trim().is_empty() {
            return Err(StudyError::MissingToken);
        }
        if parsed.study_id.trim().is_empty() {
            return Err(StudyError::MissingStudyId);
        }
        Ok(parsed)
    }
}

#[derive(Debug, Clone)]
pub struct StudyChapterImport {
    pub study_id: Option<String>,
    pub name: Option<String>,
    pub pgn: String,
    pub orientation: Option<String>,
}

#[derive(Debug)]
pub struct LichessStudyClient {
    config: StudyConfig,
    http: Client,
}

impl LichessStudyClient {
    pub fn new(config: StudyConfig) -> Result<Self, StudyError> {
        let client = Client::builder()
            .user_agent("rep-grow")
            .build()
            .map_err(StudyError::Http)?;
        Ok(Self {
            config,
            http: client,
        })
    }

    pub fn import_pgn(&self, payload: &StudyChapterImport) -> Result<(), StudyError> {
        let study_id = payload
            .study_id
            .as_deref()
            .unwrap_or(self.config.study_id.as_str());
        if study_id.trim().is_empty() {
            return Err(StudyError::MissingStudyId);
        }

        let base = self.config.base_url.trim_end_matches('/');
        let url = format!("{base}/api/study/{study_id}/import-pgn");
        let mut form: Vec<(String, String)> = vec![("pgn".to_string(), payload.pgn.clone())];
        if let Some(name) = &payload.name {
            form.push(("name".to_string(), name.clone()));
        }
        if let Some(orientation) = payload
            .orientation
            .clone()
            .or_else(|| self.config.default_orientation.clone())
        {
            form.push(("orientation".to_string(), orientation));
        }

        let response = self
            .http
            .post(url)
            .bearer_auth(&self.config.token)
            .header(
                reqwest::header::CONTENT_TYPE,
                "application/x-www-form-urlencoded",
            )
            .form(&form)
            .send()?;
        if !response.status().is_success() {
            return Err(StudyError::HttpStatus(response.status()));
        }
        Ok(())
    }
}

#[derive(Debug)]
pub enum StudyError {
    Io(std::io::Error),
    ParseToml(toml::de::Error),
    Http(reqwest::Error),
    MissingToken,
    MissingStudyId,
    HttpStatus(StatusCode),
}

impl From<std::io::Error> for StudyError {
    fn from(err: std::io::Error) -> Self {
        StudyError::Io(err)
    }
}

impl From<toml::de::Error> for StudyError {
    fn from(err: toml::de::Error) -> Self {
        StudyError::ParseToml(err)
    }
}

impl From<reqwest::Error> for StudyError {
    fn from(err: reqwest::Error) -> Self {
        StudyError::Http(err)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use httpmock::Method::POST;
    use httpmock::MockServer;
    use std::fs;
    use std::io::Write;

    fn write_temp_config(dir: &std::path::Path, body: &str) -> std::path::PathBuf {
        let path = dir.join("study.toml");
        let mut file = fs::File::create(&path).expect("create config");
        file.write_all(body.as_bytes()).expect("write config");
        path
    }

    #[test]
    fn config_parses_from_toml() {
        let tmp = tempfile::tempdir().expect("temp dir");
        let config_path = write_temp_config(
            tmp.path(),
            r#"
token = "abc123"
study_id = "MyStudy"
base_url = "https://example.com"
default_orientation = "black"
"#,
        );

        let cfg = StudyConfig::from_path(&config_path).expect("parsed config");
        assert_eq!(cfg.token, "abc123");
        assert_eq!(cfg.study_id, "MyStudy");
        assert_eq!(cfg.base_url, "https://example.com");
        assert_eq!(cfg.default_orientation.as_deref(), Some("black"));
    }

    #[test]
    fn import_pgn_sends_expected_request() {
        let server = MockServer::start();
        let token = "secret";
        let study_id = "ABCDEFGH";
        let cfg = StudyConfig {
            token: token.to_string(),
            study_id: study_id.to_string(),
            base_url: server.base_url(),
            default_orientation: Some("white".to_string()),
        };

        let mock = server.mock(|when, then| {
            when.method(POST)
                .path(format!("/api/study/{study_id}/import-pgn"))
                .header("authorization", format!("Bearer {token}"))
                .body_contains("pgn=1.+e4+e5+2.+Nf3");
            then.status(200)
                .header("content-type", "application/json")
                .body(r#"{"chapters": []}"#);
        });

        let client = LichessStudyClient::new(cfg).expect("client");
        let payload = StudyChapterImport {
            study_id: None,
            name: Some("Line A".to_string()),
            pgn: "1. e4 e5 2. Nf3 Nc6 *".to_string(),
            orientation: None,
        };

        client.import_pgn(&payload).expect("import succeeds");
        mock.assert();
    }
}
