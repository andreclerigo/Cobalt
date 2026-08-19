use kobo_json::Value;

pub const MAX_ITEMS: usize = 500;
const MAX_COLLECTIONS: usize = 32;
const MAX_TAGS: usize = 20;
const MAX_AUTHORS: usize = 64;

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct Collection {
    pub key: String,
    pub name: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct Paper {
    pub key: String,
    pub version: u32,
    pub title: String,
    pub creators: String,
    pub year: String,
    pub date_added: String,
    pub tags: Vec<String>,
    pub has_pdf: bool,
}

impl Paper {
    pub fn searchable(&self, phrase: &str) -> bool {
        let phrase = phrase.to_lowercase();
        self.title.to_lowercase().contains(&phrase)
            || self.creators.to_lowercase().contains(&phrase)
            || self
                .tags
                .iter()
                .any(|tag| tag.to_lowercase().contains(&phrase))
    }

    pub fn summary(&self) -> String {
        let mut parts = Vec::new();
        if !self.creators.is_empty() {
            parts.push(self.creators.clone());
        }
        if !self.year.is_empty() {
            parts.push(self.year.clone());
        }
        if self.has_pdf {
            parts.push("PDF stored".to_owned());
        }
        parts.join(" · ")
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct Snapshot {
    pub collection_key: String,
    pub revision: String,
    pub total: usize,
    pub truncated: bool,
    pub papers: Vec<Paper>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct Detail {
    pub paper: Paper,
    pub authors: Vec<String>,
    pub abstract_text: String,
    pub venue: String,
    pub doi: String,
    pub url: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct Conversion {
    pub state: String,
    pub document_version: Option<String>,
    pub truncated: bool,
    pub message: Option<String>,
}

pub fn collections(bytes: &[u8]) -> Option<Vec<Collection>> {
    let root = parse(bytes)?;
    let rows = root.as_array()?;
    let mut parsed = Vec::new();
    for row in rows.iter().take(MAX_COLLECTIONS) {
        let key = clean(field(row, "key")?, 32);
        let name = clean(field(row, "name")?, 160);
        if valid_key(&key) && !name.is_empty() {
            parsed.push(Collection { key, name });
        }
    }
    Some(parsed)
}

pub fn snapshot(bytes: &[u8]) -> Option<Snapshot> {
    let root = parse(bytes)?;
    let rows = root.get("items")?.as_array()?;
    let mut papers = Vec::new();
    for row in rows.iter().take(MAX_ITEMS) {
        if let Some(paper) = paper(row) {
            papers.push(paper);
        }
    }
    papers.sort_by(|left, right| right.date_added.cmp(&left.date_added));
    Some(Snapshot {
        collection_key: text(root.get("collection_key"), 32),
        revision: text(root.get("revision"), 32),
        total: number(root.get("total")),
        truncated: flag(root.get("truncated")),
        papers,
    })
}

pub fn detail(bytes: &[u8]) -> Option<Detail> {
    let root = parse(bytes)?;
    let paper = paper(&root)?;
    let authors = root
        .get("authors")
        .and_then(Value::as_array)
        .map(|authors| {
            authors
                .iter()
                .filter_map(Value::as_str)
                .map(|author| clean(author, 256))
                .filter(|author| !author.is_empty())
                .take(MAX_AUTHORS)
                .collect()
        })
        .unwrap_or_default();
    Some(Detail {
        paper,
        authors,
        abstract_text: text(root.get("abstract"), 96_000),
        venue: text(root.get("venue"), 512),
        doi: text(root.get("doi"), 512),
        url: text(root.get("url"), 2_048),
    })
}

pub fn conversion(bytes: &[u8]) -> Option<Conversion> {
    let root = parse(bytes)?;
    let state = text(root.get("state"), 32);
    if !matches!(
        state.as_str(),
        "missing_pdf" | "queued" | "running" | "ready" | "failed"
    ) {
        return None;
    }
    Some(Conversion {
        state,
        document_version: root
            .get("document_version")
            .and_then(Value::as_str)
            .map(|value| clean(value, 64)),
        truncated: flag(root.get("truncated")),
        message: root
            .get("message")
            .and_then(Value::as_str)
            .map(|value| clean(value, 512)),
    })
}

fn paper(value: &Value) -> Option<Paper> {
    let key = text(value.get("key"), 32);
    let title = text(value.get("title"), 2_048);
    if !valid_key(&key) || title.is_empty() {
        return None;
    }
    let tags = value
        .get("tags")
        .and_then(Value::as_array)
        .map(|tags| {
            tags.iter()
                .filter_map(Value::as_str)
                .map(|tag| clean(tag, 64))
                .filter(|tag| !tag.is_empty())
                .take(MAX_TAGS)
                .collect()
        })
        .unwrap_or_default();
    Some(Paper {
        key,
        version: u32::try_from(number(value.get("version"))).unwrap_or(u32::MAX),
        title,
        creators: text(value.get("creator_summary"), 1_024),
        year: text(value.get("year"), 16),
        date_added: text(value.get("date_added"), 64),
        tags,
        has_pdf: flag(value.get("has_stored_pdf")),
    })
}

fn parse(bytes: &[u8]) -> Option<Value> {
    let text = std::str::from_utf8(bytes).ok()?;
    kobo_json::parse(text).ok()
}

fn field<'a>(value: &'a Value, name: &str) -> Option<&'a str> {
    value.get(name)?.as_str()
}

fn text(value: Option<&Value>, maximum: usize) -> String {
    value
        .and_then(Value::as_str)
        .map_or_else(String::new, |value| clean(value, maximum))
}

fn clean(value: &str, maximum: usize) -> String {
    value
        .replace(['\0', '\t', '\r', '\n'], " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(maximum)
        .collect()
}

fn number(value: Option<&Value>) -> usize {
    value
        .and_then(Value::as_i64)
        .and_then(|value| usize::try_from(value).ok())
        .unwrap_or(0)
}

fn flag(value: Option<&Value>) -> bool {
    value.and_then(Value::as_bool).unwrap_or(false)
}

fn valid_key(value: &str) -> bool {
    !value.is_empty() && value.len() <= 32 && value.bytes().all(|byte| byte.is_ascii_alphanumeric())
}

#[cfg(test)]
mod tests {
    use super::{collections, conversion, detail, snapshot};

    const SNAPSHOT: &[u8] = br#"{
      "collection_key":"COLL1","revision":"42","total":2,"truncated":false,"items":[
        {"key":"OLD1","version":1,"title":"Older","creator_summary":"Ada",
         "year":"2025","date_added":"2025-01-01","tags":["math"],"has_stored_pdf":false},
        {"key":"NEW1","version":2,"title":"Newer","creator_summary":"Grace",
         "year":"2026","date_added":"2026-01-01","tags":["systems"],"has_stored_pdf":true}
      ]
    }"#;

    #[test]
    fn a_snapshot_is_sorted_and_searchable() {
        let parsed = snapshot(SNAPSHOT).expect("snapshot parses");
        assert_eq!(parsed.revision, "42");
        assert_eq!(parsed.papers[0].key, "NEW1");
        assert!(parsed.papers[0].searchable("SYSTEMS"));
        assert!(!parsed.papers[0].searchable("biology"));
    }

    #[test]
    fn collections_refuse_keys_that_could_be_paths() {
        let parsed =
            collections(br#"[{"key":"GOOD1","name":"Reading"},{"key":"../bad","name":"Bad"}]"#)
                .expect("collection response parses");
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].key, "GOOD1");
    }

    #[test]
    fn detail_and_conversion_keep_expected_states() {
        let body = br#"{"key":"P1","version":1,"title":"Paper","creator_summary":"Ada",
          "year":"2026","date_added":"2026-01-01","tags":[],"has_stored_pdf":true,
          "authors":["Ada Lovelace"],"abstract":"An abstract","venue":"Journal",
          "doi":"10.1/test","url":"https://example.org"}"#;
        assert_eq!(
            detail(body).expect("detail parses").authors,
            ["Ada Lovelace"]
        );
        assert_eq!(
            conversion(
                br#"{"state":"ready","document_version":"v1","truncated":false,"message":null}"#
            )
            .expect("conversion parses")
            .state,
            "ready"
        );
        assert!(conversion(br#"{"state":"invented"}"#).is_none());
    }
}
