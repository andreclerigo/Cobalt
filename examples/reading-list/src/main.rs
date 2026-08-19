//! A personal, read-only Zotero collection on a Kobo.
//!
//! Google Scholar is an input to Zotero through its browser connector, never a
//! service this application scrapes. The reader talks only to one owner-run
//! bridge whose exact HTTPS origin is compiled into the personal Cobalt build.

mod model;

use kobo_bookview::{BookView, Step};
use kobo_read::{Memory, Outcome};
use kobo_sdk::keyboard::{Keyboard, Pressed};
use kobo_sdk::{
    action_id, ActionId, BannerLevel, Context, Credential, Glyph, KoboApp, RowLead, Screen,
    ScreenBuilder, ShelfDownload, ShelfProgress, ShelfUpload, StoreError, StoreResult, Task,
    TaskError, TaskId, TaskOutcome,
};
use model::{Collection, Conversion, Detail, Snapshot};
use std::collections::VecDeque;
use std::fmt::Write as _;
use std::process::ExitCode;

const CREDENTIAL: &str = "reading-list";
const SELECTED_KEY: &str = "selected";
const LIBRARY_KEY: &str = "library";
const STATE_KEY: &str = "reading-state";
const SNAPSHOT_BYTES: usize = 4 * 1024 * 1024;
const DETAIL_BYTES: u32 = 256 * 1024;
const DOCUMENT_BYTES: u32 = 768 * 1024;
const MAX_KEPT: usize = 96;
const POLL_SECONDS: u32 = 3;
const MAX_POLLS: u16 = 200;

const REFRESH: &str = "refresh";
const SEARCH: &str = "search";
const LIBRARY: &str = "library";
const LIST_BACK: &str = "list-back";
const LIST_NEXT: &str = "list-next";
const READ_BACK: &str = "read-back";
const READ_NEXT: &str = "read-next";
const PAPER: &str = "paper-";
const COLLECTION: &str = "collection-";
const KEPT: &str = "kept-";
const FULL_TEXT: &str = "full-text";
const KEEP: &str = "keep";
const DISCARD: &str = "discard";
const CHANGE_COLLECTION: &str = "change-collection";
const TOGGLE_READ: &str = "toggle-read";
const RETRY_MEMORY: &str = "retry-memory";

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
enum View {
    #[default]
    Setup,
    Collections,
    Feed,
    Search,
    Detail,
    Converting,
    FullText,
    Library,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Awaiting {
    Collections,
    Snapshot,
    Detail,
    StartConversion,
    PollWait,
    PollConversion,
    Document,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct Kept {
    key: String,
    title: String,
    creators: String,
    bytes: u32,
}

#[derive(Debug, Default)]
struct ReadingList {
    view: View,
    collections: Vec<Collection>,
    selected: Option<Collection>,
    snapshot: Snapshot,
    fresh_snapshot: bool,
    filtered: Vec<usize>,
    keyboard: Keyboard,
    list_page: usize,
    library_page: usize,
    detail_pages: Vec<Vec<String>>,
    detail_page: usize,
    detail: Option<Detail>,
    opened_key: Option<String>,
    opened_title: String,
    opened_creators: String,
    task: Option<(TaskId, Awaiting)>,
    poll_count: u16,
    conversion: Option<Conversion>,
    trouble: Option<String>,
    book: BookView,
    fetched_html: Option<Vec<u8>>,
    library: Vec<Kept>,
    library_loaded: bool,
    shelf_documents: Option<Vec<String>>,
    read_keys: Vec<String>,
    last_opened: Option<String>,
    from_library: bool,
    snapshot_loading: Option<(String, ShelfDownload)>,
    snapshot_upload: Option<ShelfUpload>,
    document_loading: Option<ShelfDownload>,
    document_upload: Option<ShelfUpload>,
    document_transfer: Option<Kept>,
    memory_loading: Option<(String, ShelfDownload)>,
    memory_upload: Option<ShelfUpload>,
    active_memory: Option<(String, Vec<u8>)>,
    pending_memory: Option<(String, Vec<u8>)>,
    place: Option<Memory>,
    figure_task: Option<(TaskId, String)>,
    figures: VecDeque<String>,
}

impl ReadingList {
    fn origin() -> Option<&'static str> {
        option_env!("READING_LIST_ORIGIN").filter(|origin| valid_origin(origin))
    }

    fn url(path: &str) -> Option<String> {
        Self::origin().map(|origin| format!("{origin}{path}"))
    }

    fn credential() -> Credential {
        Credential::bearer(CREDENTIAL)
    }

    fn fetch(&mut self, context: &mut Context, path: &str, maximum: u32, awaiting: Awaiting) {
        let Some(url) = Self::url(path) else {
            self.trouble = Some(
                "This personal build has no bridge origin. Rebuild with READING_LIST_ORIGIN set."
                    .to_owned(),
            );
            return;
        };
        if let Some((task, _)) = self.task.take() {
            context.cancel(task);
        }
        let work = Task::Fetch {
            url,
            offset: 0,
            max_bytes: maximum,
            credential: Some(Self::credential()),
            headers: Vec::new(),
        };
        if let Some(task) = context.spawn(work) {
            self.task = Some((task, awaiting));
        } else {
            self.trouble = Some("The device is busy. Try again.".to_owned());
        }
    }

    fn post(&mut self, context: &mut Context, path: &str, awaiting: Awaiting) {
        let Some(url) = Self::url(path) else {
            self.trouble = Some("This personal build has no bridge origin.".to_owned());
            return;
        };
        if let Some((task, _)) = self.task.take() {
            context.cancel(task);
        }
        let work = Task::Post {
            url,
            body: "{}".to_owned(),
            content_type: "application/json".to_owned(),
            credential: Some(Self::credential()),
            headers: Vec::new(),
            max_bytes: 16 * 1024,
        };
        if let Some(task) = context.spawn(work) {
            self.task = Some((task, awaiting));
        } else {
            self.trouble = Some("The device is busy. Try again.".to_owned());
        }
    }

    fn fetch_collections(&mut self, context: &mut Context) {
        self.trouble = None;
        self.view = View::Collections;
        self.fetch(context, "/v1/collections", 64 * 1024, Awaiting::Collections);
    }

    fn refresh(&mut self, context: &mut Context) {
        let Some(collection) = self.selected.clone() else {
            self.fetch_collections(context);
            return;
        };
        self.trouble = None;
        self.view = View::Feed;
        self.fetch(
            context,
            &format!("/v1/collections/{}/snapshot?limit=500", collection.key),
            u32::try_from(SNAPSHOT_BYTES).expect("snapshot ceiling fits the task protocol"),
            Awaiting::Snapshot,
        );
    }

    fn open_paper(&mut self, context: &mut Context, index: usize) {
        if self.memory_upload.is_some() || self.pending_memory.is_some() {
            self.trouble = Some(
                "Finish or retry the pending reading-memory save before opening another paper."
                    .to_owned(),
            );
            return;
        }
        let Some(paper) = self.snapshot.papers.get(index).cloned() else {
            return;
        };
        self.close_book(context);
        self.reset_item_content();
        self.opened_key = Some(paper.key.clone());
        self.opened_title.clone_from(&paper.title);
        self.opened_creators.clone_from(&paper.creators);
        self.detail = None;
        self.detail_pages.clear();
        self.detail_page = 0;
        self.from_library = false;
        self.view = View::Detail;
        self.trouble = None;
        self.fetch(
            context,
            &format!("/v1/items/{}", paper.key),
            DETAIL_BYTES,
            Awaiting::Detail,
        );
    }

    fn reset_item_content(&mut self) {
        self.fetched_html = None;
        self.conversion = None;
        self.place = None;
        self.figures.clear();
        self.memory_loading = None;
    }

    fn start_conversion(&mut self, context: &mut Context) {
        if self.memory_upload.is_some() || self.pending_memory.is_some() {
            self.trouble = Some(
                "Finish or retry the pending reading-memory save before reopening the paper."
                    .to_owned(),
            );
            return;
        }
        let Some(key) = self.opened_key.clone() else {
            return;
        };
        if self.is_kept(&key) {
            self.load_document(context, &key);
            return;
        }
        self.ask_memory(context, &key);
        self.poll_count = 0;
        self.trouble = None;
        self.view = View::Converting;
        let path = format!("/v1/items/{key}/conversion");
        self.post(context, &path, Awaiting::StartConversion);
    }

    fn poll_conversion(&mut self, context: &mut Context) {
        let Some(key) = self.opened_key.as_ref() else {
            return;
        };
        self.fetch(
            context,
            &format!("/v1/items/{key}/conversion"),
            16 * 1024,
            Awaiting::PollConversion,
        );
    }

    fn wait_to_poll(&mut self, context: &mut Context) {
        if self.poll_count >= MAX_POLLS {
            self.trouble =
                Some("Conversion took longer than ten minutes. Try again later.".to_owned());
            self.view = View::Detail;
            return;
        }
        self.poll_count += 1;
        if let Some(task) = context.spawn(Task::Sleep {
            seconds: POLL_SECONDS,
        }) {
            self.task = Some((task, Awaiting::PollWait));
        }
    }

    fn fetch_document(&mut self, context: &mut Context) {
        let Some(key) = self.opened_key.as_ref() else {
            return;
        };
        self.fetch(
            context,
            &format!("/v1/items/{key}/document"),
            DOCUMENT_BYTES,
            Awaiting::Document,
        );
    }

    fn took_conversion(&mut self, context: &mut Context, bytes: &[u8]) {
        let Some(conversion) = model::conversion(bytes) else {
            self.fail_detail("The bridge returned an unreadable conversion state.");
            return;
        };
        self.conversion = Some(conversion.clone());
        match conversion.state.as_str() {
            "ready" => self.fetch_document(context),
            "queued" | "running" => self.wait_to_poll(context),
            "missing_pdf" => self.fail_detail("No PDF is stored with this Zotero item."),
            "failed" => self.fail_detail(
                conversion
                    .message
                    .as_deref()
                    .unwrap_or("The stored PDF could not be converted."),
            ),
            _ => self.fail_detail("The bridge returned an unknown conversion state."),
        }
    }

    fn took_document(&mut self, context: &mut Context, bytes: &[u8]) {
        let Ok(html) = std::str::from_utf8(bytes) else {
            self.fail_detail("The converted document was not UTF-8 text.");
            return;
        };
        let document = kobo_doc::html::parse(html);
        if document.blocks.is_empty() {
            self.fail_detail("The conversion contained no readable text.");
            return;
        }
        self.fetched_html = Some(bytes.to_vec());
        let memory = self.place.take().unwrap_or_default();
        self.book.open(context, document, memory);
        if self
            .conversion
            .as_ref()
            .is_some_and(|conversion| conversion.truncated)
        {
            self.book.mark_truncated(true);
        }
        self.figures = self.book.missing_pictures().into_iter().collect();
        self.fetch_next_figure(context);
        if let Some(key) = self.opened_key.clone() {
            self.mark_opened(context, &key);
        }
        self.view = View::FullText;
        context.device().read_frontlight();
    }

    fn fetch_next_figure(&mut self, context: &mut Context) {
        if self.figure_task.is_some() {
            return;
        }
        let Some(key) = self.opened_key.clone() else {
            return;
        };
        while let Some(original) = self.figures.pop_front() {
            let Some(name) = original.strip_prefix("figures/") else {
                continue;
            };
            if !valid_figure(name) {
                continue;
            }
            let Some(url) = Self::url(&format!("/v1/items/{key}/figures/{name}")) else {
                return;
            };
            if let Some(task) = context.spawn(Task::Fetch {
                url,
                offset: 0,
                max_bytes: kobo_bookview::MAX_PICTURE_BYTES,
                credential: Some(Self::credential()),
                headers: Vec::new(),
            }) {
                self.figure_task = Some((task, original));
                return;
            }
            self.figures.push_front(original);
            return;
        }
        let _ = self.book.settle_pictures(context);
    }

    fn keep_document(&mut self, context: &mut Context) {
        let Some(key) = self.opened_key.clone() else {
            return;
        };
        if self.is_kept(&key) {
            return;
        }
        if self.document_upload.is_some() {
            self.trouble = Some("The offline copy is still being saved.".to_owned());
            return;
        }
        if self.library.len() >= MAX_KEPT {
            self.trouble =
                Some("Offline library full. Remove a paper before keeping another.".to_owned());
            return;
        }
        let Some(bytes) = self.fetched_html.clone() else {
            self.trouble = Some("Open the full text before keeping it offline.".to_owned());
            return;
        };
        let mut upload = ShelfUpload::new(document_blob(&key), bytes.clone());
        upload.start(context);
        self.document_upload = Some(upload);
        self.document_transfer = Some(Kept {
            key,
            title: compact(&self.opened_title, 160),
            creators: compact(&self.opened_creators, 256),
            bytes: u32::try_from(bytes.len()).unwrap_or(u32::MAX),
        });
    }

    fn discard_document(&mut self, context: &mut Context) {
        let Some(key) = self.opened_key.clone() else {
            return;
        };
        self.library.retain(|kept| kept.key != key);
        if let Some(documents) = &mut self.shelf_documents {
            documents.retain(|document| document != &key);
        }
        context.shelf().remove(document_blob(&key));
        context.shelf().remove(memory_blob(&key));
        self.save_library(context);
    }

    fn load_document(&mut self, context: &mut Context, key: &str) {
        self.ask_memory(context, key);
        let mut download = ShelfDownload::new(document_blob(key)).at_most(DOCUMENT_BYTES as usize);
        download.start(context);
        self.document_loading = Some(download);
        self.view = View::Converting;
    }

    fn ask_memory(&mut self, context: &mut Context, key: &str) {
        self.place = None;
        let mut download =
            ShelfDownload::new(memory_blob(key)).at_most(kobo_sdk::MAX_SHELF_DOWNLOAD);
        download.start(context);
        self.memory_loading = Some((key.to_owned(), download));
    }

    fn save_memory(&mut self, context: &mut Context) {
        let Some((name, bytes)) = self.memory_payload() else {
            return;
        };
        if self.memory_upload.is_some() {
            self.pending_memory = Some((name, bytes));
            return;
        }
        self.pending_memory = None;
        self.start_memory_upload(context, name, bytes);
    }

    fn memory_payload(&self) -> Option<(String, Vec<u8>)> {
        let key = self.opened_key.as_ref()?;
        let memory = self.book.memory()?;
        Some((memory_blob(key), memory.encode()))
    }

    fn start_memory_upload(&mut self, context: &mut Context, name: String, bytes: Vec<u8>) {
        let mut upload = ShelfUpload::new(name.clone(), bytes.clone());
        upload.start(context);
        self.memory_upload = Some(upload);
        self.active_memory = Some((name, bytes));
    }

    fn retry_memory(&mut self, context: &mut Context) {
        if self.memory_upload.is_some() {
            self.trouble = Some("Reading memory is still being saved.".to_owned());
            return;
        }
        let Some((name, bytes)) = self.pending_memory.take() else {
            return;
        };
        self.trouble = None;
        self.start_memory_upload(context, name, bytes);
    }

    fn close_book(&mut self, context: &mut Context) {
        self.save_memory(context);
        self.book.close(context);
        self.figures.clear();
        if let Some((task, _)) = self.figure_task.take() {
            context.cancel(task);
        }
        self.place = None;
        self.memory_loading = None;
    }

    fn select_collection(&mut self, context: &mut Context, index: usize) {
        let Some(collection) = self.collections.get(index).cloned() else {
            return;
        };
        context
            .store()
            .save(SELECTED_KEY, encode_selection(&collection));
        self.selected = Some(collection);
        self.snapshot = Snapshot::default();
        self.fresh_snapshot = false;
        self.filtered.clear();
        self.snapshot_loading = None;
        self.start_snapshot_load(context);
        self.refresh(context);
    }

    fn start_snapshot_load(&mut self, context: &mut Context) {
        if self.snapshot_loading.is_some() {
            return;
        }
        let Some(collection) = self.selected.as_ref() else {
            return;
        };
        let key = collection.key.clone();
        let mut download = ShelfDownload::new(snapshot_blob(&key)).at_most(SNAPSHOT_BYTES);
        download.start(context);
        self.snapshot_loading = Some((key, download));
    }

    fn save_snapshot(&mut self, context: &mut Context, bytes: Vec<u8>) {
        let Some(collection) = self.selected.as_ref() else {
            return;
        };
        let mut upload = ShelfUpload::new(snapshot_blob(&collection.key), bytes);
        upload.start(context);
        self.snapshot_upload = Some(upload);
    }

    fn apply_snapshot(&mut self, snapshot: Snapshot) {
        self.snapshot = snapshot;
        self.filtered = (0..self.snapshot.papers.len()).collect();
        self.list_page = 0;
    }

    fn apply_search(&mut self) {
        let phrase = self.keyboard.text().trim();
        self.filtered = self
            .snapshot
            .papers
            .iter()
            .enumerate()
            .filter_map(|(index, paper)| paper.searchable(phrase).then_some(index))
            .collect();
        self.list_page = 0;
    }

    fn is_kept(&self, key: &str) -> bool {
        self.library.iter().any(|kept| kept.key == key)
    }

    fn save_library(&mut self, context: &mut Context) {
        context
            .store()
            .save(LIBRARY_KEY, encode_library(&self.library));
    }

    fn reconcile_shelf(&mut self, context: &mut Context) {
        if !self.library_loaded {
            return;
        }
        let Some(documents) = self.shelf_documents.as_ref() else {
            return;
        };
        for key in documents {
            if !self.library.iter().any(|kept| &kept.key == key) {
                context.shelf().remove(document_blob(key));
            }
        }
        let before = self.library.len();
        self.library
            .retain(|kept| documents.iter().any(|key| key == &kept.key));
        if self.library.len() != before {
            self.save_library(context);
        }
    }

    fn mark_opened(&mut self, context: &mut Context, key: &str) {
        self.last_opened = Some(key.to_owned());
        if !self.read_keys.iter().any(|read| read == key) {
            if self.read_keys.len() >= model::MAX_ITEMS {
                self.read_keys.remove(0);
            }
            self.read_keys.push(key.to_owned());
        }
        context.store().save(
            STATE_KEY,
            encode_reading_state(&self.read_keys, self.last_opened.as_deref()),
        );
    }

    fn toggle_read(&mut self, context: &mut Context) {
        let Some(key) = self.opened_key.as_ref() else {
            return;
        };
        if self.read_keys.iter().any(|read| read == key) {
            self.read_keys.retain(|read| read != key);
        } else {
            self.read_keys.push(key.clone());
        }
        context.store().save(
            STATE_KEY,
            encode_reading_state(&self.read_keys, self.last_opened.as_deref()),
        );
    }

    fn fail_detail(&mut self, message: &str) {
        self.trouble = Some(message.to_owned());
        self.view = View::Detail;
    }

    fn setup_screen(&self) -> Screen {
        let mut screen = ScreenBuilder::new("reading-list-setup").top_bar("Reading List");
        if let Some(trouble) = &self.trouble {
            screen = screen.banner(BannerLevel::Attention, trouble.clone());
        }
        if Self::origin().is_none() {
            return screen
                .empty_state(
                    "This personal app needs an exact bridge origin. Build Cobalt with \n\
                     READING_LIST_ORIGIN=https://your-host before installing it.",
                )
                .build();
        }
        screen
            .empty_state(
                "Install the bridge bearer token with: kobo secret set reading-list \
                 --device <address>. Then choose the Zotero collection exposed by your bridge.",
            )
            .bottom_action_marked(REFRESH, "Connect to bridge", Glyph::Download)
            .build()
    }

    fn collections_screen(&self) -> Screen {
        let mut screen =
            ScreenBuilder::new("reading-list-collections").top_bar("Choose collection");
        if let Some(trouble) = &self.trouble {
            screen = screen.banner(BannerLevel::Attention, trouble.clone());
        }
        if self
            .task
            .is_some_and(|(_, awaiting)| awaiting == Awaiting::Collections)
        {
            return screen
                .activity("Reading allowed Zotero collections", None)
                .build();
        }
        if self.collections.is_empty() {
            return screen
                .empty_state("The bridge exposed no allowed collections.")
                .bottom_action_marked(REFRESH, "Try again", Glyph::Download)
                .build();
        }
        screen
            .rows(
                self.collections
                    .iter()
                    .enumerate()
                    .map(|(index, collection)| {
                        (
                            format!("{COLLECTION}{index}"),
                            collection.name.clone(),
                            collection.key.clone(),
                            RowLead::Icon(Glyph::Bookmark),
                        )
                    }),
            )
            .build()
    }

    fn feed_screen(&self, context: &Context) -> Screen {
        let title = self
            .selected
            .as_ref()
            .map_or("Reading List", |collection| collection.name.as_str());
        let mut screen = ScreenBuilder::new("reading-list-feed").top_bar(title);
        if let Some(trouble) = &self.trouble {
            screen = screen.banner(BannerLevel::Attention, trouble.clone());
        } else if self.snapshot.truncated {
            screen = screen.banner(
                BannerLevel::Info,
                format!(
                    "Showing the newest {} of {} items.",
                    self.snapshot.papers.len(),
                    self.snapshot.total
                ),
            );
        } else if let Some(last) = self
            .last_opened
            .as_deref()
            .and_then(|key| self.snapshot.papers.iter().find(|paper| paper.key == key))
        {
            screen = screen.banner(BannerLevel::Info, format!("Last opened: {}", last.title));
        }
        if self.snapshot.papers.is_empty()
            && self
                .task
                .is_some_and(|(_, awaiting)| awaiting == Awaiting::Snapshot)
        {
            return screen.skeleton(6).build();
        }
        if self.filtered.is_empty() {
            return screen
                .empty_state("No cached papers match this list or search.")
                .top_bar_glyph(LIBRARY, "Offline", Glyph::Bookmark)
                .bottom_action_marked(REFRESH, "Refresh", Glyph::Download)
                .bottom_action_marked(SEARCH, "Search cached papers", Glyph::Search)
                .build();
        }
        let rows: Vec<(String, String)> = self
            .filtered
            .iter()
            .filter_map(|index| self.snapshot.papers.get(*index))
            .map(|paper| (paper.title.clone(), self.paper_summary(paper)))
            .collect();
        let borrowed: Vec<(&str, &str)> = rows
            .iter()
            .map(|(title, summary)| (title.as_str(), summary.as_str()))
            .collect();
        let pages = context.paginate_rows(&borrowed, true);
        let page = self.list_page.min(pages.len().saturating_sub(1));
        let shown = pages.get(page).map(Vec::as_slice).unwrap_or_default();
        screen = screen.rows(shown.iter().filter_map(|shown_index| {
            let paper_index = *self.filtered.get(*shown_index)?;
            let paper = self.snapshot.papers.get(paper_index)?;
            Some((
                format!("{PAPER}{paper_index}"),
                paper.title.clone(),
                self.paper_summary(paper),
                RowLead::Number(u16::try_from(*shown_index + 1).unwrap_or(u16::MAX)),
            ))
        }));
        screen
            .top_bar_glyph(LIBRARY, "Offline", Glyph::Bookmark)
            .bottom_action_marked(REFRESH, "Refresh", Glyph::Download)
            .bottom_action_marked(SEARCH, "Search cached papers", Glyph::Search)
            .page_turns(LIST_BACK, LIST_NEXT)
            .page_position(page_number(page), page_total(pages.len()))
            .build()
    }

    fn search_screen(&self) -> Screen {
        ScreenBuilder::new("reading-list-search")
            .top_bar("Search cached papers")
            .typed(&self.keyboard, "Title, author, or tag")
            .keyboard(&self.keyboard, "Search")
            .build()
    }

    fn detail_screen(&self) -> Screen {
        let mut screen = ScreenBuilder::new("reading-list-detail").top_bar(&self.opened_title);
        if let Some(trouble) = &self.trouble {
            screen = screen.banner(BannerLevel::Attention, trouble.clone());
        }
        if self
            .task
            .is_some_and(|(_, awaiting)| awaiting == Awaiting::Detail)
        {
            return screen.activity("Fetching paper details", None).build();
        }
        let page = self
            .detail_page
            .min(self.detail_pages.len().saturating_sub(1));
        for line in self
            .detail_pages
            .get(page)
            .map(Vec::as_slice)
            .unwrap_or_default()
        {
            screen = screen.text(line.clone());
        }
        let has_pdf = self
            .detail
            .as_ref()
            .is_some_and(|detail| detail.paper.has_pdf);
        let kept = self
            .opened_key
            .as_deref()
            .is_some_and(|key| self.is_kept(key));
        if has_pdf || kept {
            screen = screen.bottom_action_marked(
                FULL_TEXT,
                if kept {
                    "Open offline text"
                } else {
                    "Convert full text"
                },
                Glyph::Book,
            );
        }
        if kept {
            screen = screen.bottom_action_marked(DISCARD, "Remove offline", Glyph::Trash);
        } else if self.fetched_html.is_some() {
            screen = screen.bottom_action_marked(KEEP, "Keep text offline", Glyph::Download);
        }
        let read = self
            .opened_key
            .as_ref()
            .is_some_and(|key| self.read_keys.iter().any(|read| read == key));
        screen = screen.bottom_action_marked(
            TOGGLE_READ,
            if read { "Mark unread" } else { "Mark read" },
            Glyph::Check,
        );
        if self.pending_memory.is_some() && self.memory_upload.is_none() {
            screen = screen.bottom_action_marked(
                RETRY_MEMORY,
                "Retry saving reading memory",
                Glyph::Download,
            );
        }
        screen
            .page_turns(READ_BACK, READ_NEXT)
            .page_position(page_number(page), page_total(self.detail_pages.len()))
            .build()
    }

    fn converting_screen(&self) -> Screen {
        let text = if self.document_loading.is_some() {
            "Opening offline text"
        } else if self
            .task
            .is_some_and(|(_, awaiting)| awaiting == Awaiting::Document)
        {
            "Fetching converted text"
        } else {
            "Converting the stored PDF"
        };
        ScreenBuilder::new("reading-list-converting")
            .top_bar(&self.opened_title)
            .activity(text, None)
            .text("The first conversion can take several minutes.")
            .build()
    }

    fn full_text_screen(&self) -> Screen {
        self.book
            .screen(&self.opened_title)
            .unwrap_or_else(|| self.detail_screen())
    }

    fn library_screen(&self, context: &Context) -> Screen {
        let mut screen = ScreenBuilder::new("reading-list-library").top_bar("Offline papers");
        if let Some(trouble) = &self.trouble {
            screen = screen.banner(BannerLevel::Attention, trouble.clone());
        }
        if self.library.is_empty() {
            return screen
                .empty_state("No converted papers are kept on this reader yet.")
                .build();
        }
        let rows: Vec<(String, String)> = self
            .library
            .iter()
            .map(|kept| {
                (
                    kept.title.clone(),
                    format!("{} · {} KB", kept.creators, kept.bytes / 1024),
                )
            })
            .collect();
        let borrowed: Vec<(&str, &str)> = rows
            .iter()
            .map(|(title, summary)| (title.as_str(), summary.as_str()))
            .collect();
        let pages = context.paginate_rows(&borrowed, true);
        let page = self.library_page.min(pages.len().saturating_sub(1));
        let shown = pages.get(page).map(Vec::as_slice).unwrap_or_default();
        screen
            .rows(shown.iter().filter_map(|index| {
                self.library.get(*index).map(|kept| {
                    (
                        format!("{KEPT}{index}"),
                        kept.title.clone(),
                        format!("{} · {} KB", kept.creators, kept.bytes / 1024),
                        RowLead::Icon(Glyph::Bookmark),
                    )
                })
            }))
            .page_turns(LIST_BACK, LIST_NEXT)
            .page_position(page_number(page), page_total(pages.len()))
            .build()
    }

    fn show(&mut self, context: &mut Context) {
        let screen = match self.view {
            View::Setup => self.setup_screen(),
            View::Collections => self.collections_screen(),
            View::Feed => self.feed_screen(context),
            View::Search => self.search_screen(),
            View::Detail => self.detail_screen(),
            View::Converting => self.converting_screen(),
            View::FullText => self.full_text_screen(),
            View::Library => self.library_screen(context),
        };
        context.set_screen(screen.with_own_back(self.view != View::Setup));
    }

    fn paper_summary(&self, paper: &model::Paper) -> String {
        let mut summary = paper.summary();
        if self.read_keys.iter().any(|key| key == &paper.key) {
            if !summary.is_empty() {
                summary.push_str(" · ");
            }
            summary.push_str("Read");
        }
        summary
    }

    fn turn(&mut self, context: &mut Context, forward: bool) {
        let page = match self.view {
            View::Feed => &mut self.list_page,
            View::Library => &mut self.library_page,
            View::Detail => &mut self.detail_page,
            _ => return,
        };
        if forward {
            *page = page.saturating_add(1);
        } else {
            *page = page.saturating_sub(1);
        }
        self.show(context);
    }

    fn back(&mut self, context: &mut Context) {
        match self.view {
            View::Collections => {
                self.view = if self.selected.is_some() {
                    View::Feed
                } else {
                    View::Setup
                };
            }
            View::Search | View::Library => self.view = View::Feed,
            View::Detail => {
                self.view = if self.from_library {
                    View::Library
                } else {
                    View::Feed
                }
            }
            View::Converting => {
                if let Some((task, _)) = self.task.take() {
                    context.cancel(task);
                }
                self.document_loading = None;
                self.memory_loading = None;
                self.view = View::Detail;
            }
            View::FullText => {
                self.close_book(context);
                self.view = if self.from_library {
                    View::Library
                } else {
                    View::Detail
                };
            }
            View::Feed | View::Setup => return,
        }
        self.show(context);
    }
}

impl KoboApp for ReadingList {
    fn on_start(&mut self, context: &mut Context) {
        context.store().load(SELECTED_KEY);
        context.store().load(LIBRARY_KEY);
        context.store().load(STATE_KEY);
        context.shelf().list();
        self.show(context);
    }

    #[allow(clippy::too_many_lines)]
    fn on_action(&mut self, context: &mut Context, action: ActionId) {
        if self.view == View::Search {
            match self.keyboard.press(action) {
                Some(Pressed::Submitted) => {
                    self.apply_search();
                    self.view = View::Feed;
                    self.show(context);
                    return;
                }
                Some(Pressed::Edited | Pressed::Shifted) => {
                    self.show(context);
                    return;
                }
                None => {}
            }
        }
        if self.view == View::FullText {
            if let Some(outcome) = self.book.act(context, action) {
                match outcome {
                    Outcome::Close => self.back(context),
                    Outcome::Light(level) => context.device().set_frontlight(level),
                    Outcome::Save => self.save_memory(context),
                    Outcome::Elsewhere | Outcome::Repaint => self.show(context),
                }
                return;
            }
        }
        if action == ActionId::BACK {
            self.back(context);
            return;
        }
        if action == action_id(REFRESH) {
            if self.view == View::Setup || self.view == View::Collections {
                self.fetch_collections(context);
            } else {
                self.refresh(context);
            }
            self.show(context);
            return;
        }
        if action == action_id(SEARCH) {
            self.keyboard.clear();
            self.view = View::Search;
            self.show(context);
            return;
        }
        if action == action_id(LIBRARY) {
            self.trouble = None;
            self.view = View::Library;
            self.library_page = 0;
            self.show(context);
            return;
        }
        if action == action_id(CHANGE_COLLECTION) {
            self.fetch_collections(context);
            self.show(context);
            return;
        }
        if action == action_id(FULL_TEXT) {
            self.start_conversion(context);
            self.show(context);
            return;
        }
        if action == action_id(KEEP) {
            self.keep_document(context);
            self.show(context);
            return;
        }
        if action == action_id(DISCARD) {
            self.discard_document(context);
            self.show(context);
            return;
        }
        if action == action_id(TOGGLE_READ) {
            self.toggle_read(context);
            self.show(context);
            return;
        }
        if action == action_id(RETRY_MEMORY) {
            self.retry_memory(context);
            self.show(context);
            return;
        }
        if action == action_id(LIST_BACK) || action == action_id(READ_BACK) {
            self.turn(context, false);
            return;
        }
        if action == action_id(LIST_NEXT) || action == action_id(READ_NEXT) {
            self.turn(context, true);
            return;
        }
        for index in 0..self.collections.len() {
            if action == action_id(&format!("{COLLECTION}{index}")) {
                self.select_collection(context, index);
                self.show(context);
                return;
            }
        }
        for index in 0..self.snapshot.papers.len() {
            if action == action_id(&format!("{PAPER}{index}")) {
                self.open_paper(context, index);
                self.show(context);
                return;
            }
        }
        for index in 0..self.library.len() {
            if action == action_id(&format!("{KEPT}{index}")) {
                if self.memory_upload.is_some() || self.pending_memory.is_some() {
                    self.trouble = Some(
                        "Finish or retry the pending reading-memory save before opening another paper."
                            .to_owned(),
                    );
                    self.show(context);
                    return;
                }
                let Some(kept) = self.library.get(index).cloned() else {
                    return;
                };
                self.close_book(context);
                self.reset_item_content();
                self.opened_key = Some(kept.key.clone());
                self.opened_title = kept.title;
                self.opened_creators = kept.creators;
                self.from_library = true;
                self.detail = None;
                self.detail_page = 0;
                self.detail_pages = vec![vec![
                    "This converted document is kept for offline text reading.".to_owned(),
                ]];
                self.view = View::Detail;
                self.show(context);
                return;
            }
        }
    }

    fn on_device_result(
        &mut self,
        context: &mut Context,
        _request: kobo_sdk::DeviceRequest,
        result: kobo_sdk::DeviceResult,
    ) {
        if let kobo_sdk::DeviceResult::Frontlight { percent } = result {
            if self.book.took_light(percent) {
                self.show(context);
            }
        }
    }

    fn on_task(&mut self, context: &mut Context, task: TaskId, outcome: TaskOutcome) {
        match self.book.woke(context, task, &outcome) {
            Step::Elsewhere => {}
            Step::Quiet => return,
            Step::Repaint => {
                self.show(context);
                return;
            }
        }
        if let Some((_, name)) = self.figure_task.take_if(|(figure, _)| *figure == task) {
            if let TaskOutcome::Completed(bytes) = &outcome {
                self.book.provide_picture(&name, bytes.clone());
            }
            self.fetch_next_figure(context);
            self.show(context);
            return;
        }
        let Some((waiting, awaiting)) = self.task else {
            return;
        };
        if waiting != task {
            return;
        }
        self.task = None;
        match outcome {
            TaskOutcome::Completed(bytes) => match awaiting {
                Awaiting::Collections => {
                    if let Some(collections) = model::collections(&bytes) {
                        self.collections = collections;
                        if self.collections.len() == 1 && self.selected.is_none() {
                            self.select_collection(context, 0);
                        }
                    } else {
                        self.trouble = Some("The collection list was unreadable.".to_owned());
                    }
                }
                Awaiting::Snapshot => {
                    if let Some(snapshot) = model::snapshot(&bytes) {
                        let expected = self.selected.as_ref().map(|one| one.key.as_str());
                        if expected == Some(snapshot.collection_key.as_str()) {
                            self.fresh_snapshot = true;
                            self.apply_snapshot(snapshot);
                            self.save_snapshot(context, bytes);
                        } else {
                            self.trouble = Some(
                                "The bridge returned a snapshot for another collection.".to_owned(),
                            );
                        }
                    } else {
                        self.trouble =
                            Some("The refreshed reading list was unreadable.".to_owned());
                    }
                }
                Awaiting::Detail => {
                    if let Some(detail) = model::detail(&bytes) {
                        self.opened_creators.clone_from(&detail.paper.creators);
                        self.detail_pages = context.paginate_reading(&detail_text(&detail), false);
                        self.detail = Some(detail);
                    } else {
                        self.trouble = Some("The paper details were unreadable.".to_owned());
                    }
                }
                Awaiting::StartConversion | Awaiting::PollConversion => {
                    self.took_conversion(context, &bytes);
                }
                Awaiting::PollWait => self.poll_conversion(context),
                Awaiting::Document => self.took_document(context, &bytes),
            },
            TaskOutcome::Failed(error) => {
                self.trouble = Some(explain(error));
                if matches!(
                    awaiting,
                    Awaiting::StartConversion | Awaiting::PollConversion | Awaiting::Document
                ) {
                    self.view = View::Detail;
                }
            }
            TaskOutcome::Cancelled => {}
        }
        self.show(context);
    }

    #[allow(clippy::too_many_lines)]
    fn on_store(&mut self, context: &mut Context, result: StoreResult) {
        if let Some((_, download)) = &mut self.snapshot_loading {
            match download.advance(context, &result) {
                ShelfProgress::Done => {
                    let (key, download) = self.snapshot_loading.take().expect("snapshot load");
                    let bytes = download.take();
                    if !self.fresh_snapshot {
                        if let Some(snapshot) = model::snapshot(&bytes) {
                            if snapshot.collection_key == key
                                && self.selected.as_ref().map(|one| one.key.as_str())
                                    == Some(key.as_str())
                            {
                                self.apply_snapshot(snapshot);
                                self.view = View::Feed;
                            }
                        }
                    }
                }
                ShelfProgress::Failed(_) => self.snapshot_loading = None,
                ShelfProgress::Moving { .. } => return,
                ShelfProgress::Elsewhere => {}
            }
        }
        if let Some(download) = &mut self.document_loading {
            match download.advance(context, &result) {
                ShelfProgress::Done => {
                    let bytes = self.document_loading.take().expect("document load").take();
                    self.took_document(context, &bytes);
                }
                ShelfProgress::Failed(error) => {
                    self.document_loading = None;
                    if error == StoreError::Missing {
                        if let Some(key) = self.opened_key.as_deref() {
                            self.library.retain(|kept| kept.key != key);
                            self.save_library(context);
                        }
                        self.fail_detail(
                            "The interrupted offline copy was removed. Convert or keep it again.",
                        );
                    } else {
                        self.fail_detail("The offline text could not be read from storage.");
                    }
                }
                ShelfProgress::Moving { .. } => return,
                ShelfProgress::Elsewhere => {}
            }
        }
        if let Some((_, download)) = &mut self.memory_loading {
            match download.advance(context, &result) {
                ShelfProgress::Done => {
                    let (key, download) = self.memory_loading.take().expect("memory load");
                    if self.opened_key.as_deref() == Some(key.as_str()) {
                        let memory = Memory::decode(&download.take());
                        if !self.book.restore(context, memory.clone()) {
                            self.place = Some(memory);
                        }
                    }
                }
                ShelfProgress::Failed(error) => {
                    self.memory_loading = None;
                    if error == StoreError::Missing {
                        self.place = Some(Memory::default());
                    } else {
                        self.trouble = Some(
                            "Reading position and annotations could not be loaded.".to_owned(),
                        );
                    }
                }
                ShelfProgress::Moving { .. } => return,
                ShelfProgress::Elsewhere => {}
            }
        }
        if let Some(upload) = &mut self.snapshot_upload {
            match upload.advance(context, &result) {
                ShelfProgress::Done | ShelfProgress::Failed(_) => self.snapshot_upload = None,
                ShelfProgress::Moving { .. } => return,
                ShelfProgress::Elsewhere => {}
            }
        }
        if let Some(upload) = &mut self.document_upload {
            match upload.advance(context, &result) {
                ShelfProgress::Done => {
                    self.document_upload = None;
                    if let Some(kept) = self.document_transfer.take() {
                        if let Some(documents) = &mut self.shelf_documents {
                            if !documents.iter().any(|key| key == &kept.key) {
                                documents.push(kept.key.clone());
                            }
                        }
                        self.library.insert(0, kept);
                        self.save_library(context);
                    }
                }
                ShelfProgress::Failed(_) => {
                    self.document_upload = None;
                    self.document_transfer = None;
                    self.trouble = Some("The converted text could not be kept.".to_owned());
                }
                ShelfProgress::Moving { .. } => return,
                ShelfProgress::Elsewhere => {}
            }
        }
        if let Some(upload) = &mut self.memory_upload {
            match upload.advance(context, &result) {
                ShelfProgress::Done => {
                    self.memory_upload = None;
                    self.active_memory = None;
                    if let Some((name, bytes)) = self.pending_memory.take() {
                        self.start_memory_upload(context, name, bytes);
                    }
                }
                ShelfProgress::Failed(error) => {
                    self.memory_upload = None;
                    if self.pending_memory.is_none() {
                        self.pending_memory = self.active_memory.take();
                    } else {
                        self.active_memory = None;
                    }
                    self.trouble = Some(format!(
                        "Reading position and annotations are not saved ({error}). Retry from paper details."
                    ));
                }
                ShelfProgress::Moving { .. } => return,
                ShelfProgress::Elsewhere => {}
            }
        }
        if let StoreResult::Loaded { key, value } = result {
            if key == SELECTED_KEY {
                self.selected = value.as_deref().and_then(decode_selection);
                if self.selected.is_some() {
                    self.view = View::Feed;
                    self.start_snapshot_load(context);
                    self.refresh(context);
                } else {
                    self.fetch_collections(context);
                }
            } else if key == LIBRARY_KEY {
                self.library = value.as_deref().map(decode_library).unwrap_or_default();
                self.library_loaded = true;
                self.reconcile_shelf(context);
            } else if key == STATE_KEY {
                (self.read_keys, self.last_opened) = value
                    .as_deref()
                    .map(decode_reading_state)
                    .unwrap_or_default();
            }
        } else if let StoreResult::Shelf(blobs) = result {
            self.shelf_documents = Some(
                blobs
                    .into_iter()
                    .filter_map(|(name, _)| {
                        name.strip_prefix("document.")
                            .filter(|key| valid_key(key))
                            .map(str::to_owned)
                    })
                    .collect(),
            );
            self.reconcile_shelf(context);
        }
        self.show(context);
    }

    fn on_background(&mut self, context: &mut Context) {
        self.save_memory(context);
    }

    fn on_suspend(&mut self, context: &mut Context) {
        self.save_memory(context);
    }

    fn on_exit(&mut self, context: &mut Context) {
        // Exit callbacks cannot wait for chunked Shelf replies. Reading
        // actions, close, background, and suspend start persistence while the
        // event loop is still available to complete it.
        self.book.close(context);
    }
}

fn detail_text(detail: &Detail) -> String {
    let mut text = String::new();
    if !detail.authors.is_empty() {
        let _ = writeln!(text, "{}", detail.authors.join(", "));
    }
    let mut facts = Vec::new();
    if !detail.venue.is_empty() {
        facts.push(detail.venue.clone());
    }
    if !detail.paper.year.is_empty() {
        facts.push(detail.paper.year.clone());
    }
    if !detail.doi.is_empty() {
        facts.push(format!("DOI {}", detail.doi));
    }
    if !facts.is_empty() {
        let _ = writeln!(text, "{}", facts.join(" · "));
    }
    if !detail.url.is_empty() {
        let _ = writeln!(text, "Source {}", detail.url);
    }
    if detail.abstract_text.is_empty() {
        text.push_str("No abstract is stored for this item.");
    } else {
        let _ = writeln!(text, "\n{}", detail.abstract_text);
    }
    text
}

fn encode_selection(collection: &Collection) -> Vec<u8> {
    format!("{}\t{}", collection.key, compact(&collection.name, 160)).into_bytes()
}

fn decode_selection(bytes: &[u8]) -> Option<Collection> {
    let text = std::str::from_utf8(bytes).ok()?;
    let (key, name) = text.split_once('\t')?;
    if !valid_key(key) || name.is_empty() {
        return None;
    }
    Some(Collection {
        key: key.to_owned(),
        name: compact(name, 160),
    })
}

fn encode_library(library: &[Kept]) -> Vec<u8> {
    let mut text = String::new();
    for kept in library.iter().take(MAX_KEPT) {
        let _ = writeln!(
            text,
            "{}\t{}\t{}\t{}",
            kept.key,
            kept.bytes,
            compact(&kept.title, 160),
            compact(&kept.creators, 256)
        );
    }
    text.into_bytes()
}

fn decode_library(bytes: &[u8]) -> Vec<Kept> {
    let Ok(text) = std::str::from_utf8(bytes) else {
        return Vec::new();
    };
    text.lines()
        .take(MAX_KEPT)
        .filter_map(|line| {
            let mut fields = line.split('\t');
            let key = fields.next()?;
            let bytes = fields.next()?.parse().ok()?;
            let title = fields.next()?;
            let creators = fields.next().unwrap_or_default();
            valid_key(key).then(|| Kept {
                key: key.to_owned(),
                title: compact(title, 160),
                creators: compact(creators, 256),
                bytes,
            })
        })
        .collect()
}

fn encode_reading_state(read_keys: &[String], last_opened: Option<&str>) -> Vec<u8> {
    let mut text = format!("last\t{}\n", last_opened.unwrap_or_default());
    for key in read_keys
        .iter()
        .filter(|key| valid_key(key))
        .take(model::MAX_ITEMS)
    {
        let _ = writeln!(text, "read\t{key}");
    }
    text.into_bytes()
}

fn decode_reading_state(bytes: &[u8]) -> (Vec<String>, Option<String>) {
    let Ok(text) = std::str::from_utf8(bytes) else {
        return (Vec::new(), None);
    };
    let mut read = Vec::new();
    let mut last = None;
    for line in text.lines().take(model::MAX_ITEMS + 1) {
        let Some((kind, key)) = line.split_once('\t') else {
            continue;
        };
        if !valid_key(key) {
            continue;
        }
        match kind {
            "last" => last = Some(key.to_owned()),
            "read" if !read.iter().any(|existing| existing == key) => read.push(key.to_owned()),
            _ => {}
        }
    }
    (read, last)
}

fn document_blob(key: &str) -> String {
    format!("document.{key}")
}

fn snapshot_blob(collection_key: &str) -> String {
    format!("snapshot.{collection_key}")
}

fn memory_blob(key: &str) -> String {
    format!("memory.{key}")
}

fn compact(text: &str, maximum: usize) -> String {
    text.replace(['\t', '\r', '\n'], " ")
        .chars()
        .take(maximum)
        .collect()
}

fn valid_key(key: &str) -> bool {
    !key.is_empty() && key.len() <= 32 && key.bytes().all(|byte| byte.is_ascii_alphanumeric())
}

fn valid_figure(name: &str) -> bool {
    let Some(number_and_extension) = name.strip_prefix("figure-") else {
        return false;
    };
    let Some((number, extension)) = number_and_extension.split_once('.') else {
        return false;
    };
    number.len() == 3
        && number.bytes().all(|byte| byte.is_ascii_digit())
        && matches!(extension, "png" | "jpg")
}

fn valid_origin(origin: &str) -> bool {
    let Some(host) = origin.strip_prefix("https://") else {
        return false;
    };
    !host.is_empty()
        && !host.contains(['/', '?', '#', '@', ':'])
        && host
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-'))
}

fn explain(error: TaskError) -> String {
    match error {
        TaskError::NoCredential => {
            "Install the token with: kobo secret set reading-list --device <address>.".to_owned()
        }
        TaskError::Offline => {
            "This reader is offline. Cached papers are still available.".to_owned()
        }
        TaskError::Denied => {
            "Reading List is not allowed to reach this bridge endpoint.".to_owned()
        }
        TaskError::Unauthorized => "The bridge rejected the Reading List bearer token.".to_owned(),
        TaskError::TooLarge => "The bridge response exceeded the app's safety limit.".to_owned(),
        TaskError::TimedOut => "The bridge took too long to answer.".to_owned(),
        TaskError::NotFound => {
            "The requested paper or conversion is no longer available.".to_owned()
        }
        TaskError::Unreachable => "The configured bridge could not be reached.".to_owned(),
    }
}

fn page_number(index: usize) -> u16 {
    u16::try_from(index + 1).unwrap_or(u16::MAX)
}

fn page_total(pages: usize) -> u16 {
    u16::try_from(pages.max(1)).unwrap_or(u16::MAX)
}

fn main() -> ExitCode {
    match kobo_sdk::run("reading-list", ReadingList::default()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("reading-list: {error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        decode_library, decode_reading_state, decode_selection, encode_library,
        encode_reading_state, encode_selection, valid_figure, valid_origin, Collection, Kept,
    };
    use kobo_sdk::{action_id, AppRunner, Command, ShelfUpload, StoreError, StoreResult};

    #[test]
    fn only_an_exact_bare_https_origin_is_accepted() {
        assert!(valid_origin("https://papers.example.com"));
        for invalid in [
            "http://papers.example.com",
            "https://papers.example.com/path",
            "https://user@papers.example.com",
            "https://papers.example.com:8443",
            "https://papers.example.com?x=1",
        ] {
            assert!(!valid_origin(invalid), "accepted {invalid}");
        }
    }

    #[test]
    fn figure_names_cannot_walk_or_change_type() {
        assert!(valid_figure("figure-001.png"));
        assert!(valid_figure("figure-064.jpg"));
        assert!(!valid_figure("../figure-001.png"));
        assert!(!valid_figure("figure-1.png"));
        assert!(!valid_figure("figure-001.svg"));
    }

    #[test]
    fn selection_and_library_round_trip_without_forging_rows() {
        let collection = Collection {
            key: "COLL1".to_owned(),
            name: "Read\nnext".to_owned(),
        };
        assert_eq!(
            decode_selection(&encode_selection(&collection)),
            Some(Collection {
                key: "COLL1".to_owned(),
                name: "Read next".to_owned(),
            })
        );
        let library = vec![Kept {
            key: "ITEM1".to_owned(),
            title: "A\tPaper".to_owned(),
            creators: "Ada".to_owned(),
            bytes: 42,
        }];
        assert_eq!(
            decode_library(&encode_library(&library))[0].title,
            "A Paper"
        );
        let state = encode_reading_state(&["ITEM1".to_owned(), "../bad".to_owned()], Some("ITEM1"));
        assert_eq!(
            decode_reading_state(&state),
            (vec!["ITEM1".to_owned()], Some("ITEM1".to_owned()))
        );
    }

    #[test]
    fn moving_to_another_item_drops_the_previous_document() {
        let mut app = super::ReadingList {
            fetched_html: Some(b"paper A".to_vec()),
            conversion: Some(super::Conversion {
                state: "ready".to_owned(),
                ..super::Conversion::default()
            }),
            ..super::ReadingList::default()
        };

        app.reset_item_content();

        assert!(app.fetched_html.is_none());
        assert!(app.conversion.is_none());
    }

    #[test]
    fn failed_memory_save_keeps_the_latest_bytes_for_an_explicit_retry() {
        let latest = ("memory.ITEM1".to_owned(), vec![9; 300_000]);
        let mut runner = AppRunner::new(super::ReadingList {
            view: super::View::Detail,
            memory_upload: Some(ShelfUpload::new("memory.ITEM1", vec![1; 300_000])),
            active_memory: Some(("memory.ITEM1".to_owned(), vec![1; 300_000])),
            pending_memory: Some(latest.clone()),
            ..super::ReadingList::default()
        });

        runner.store_result(StoreResult::Denied(StoreError::TooFull));

        assert_eq!(runner.app().pending_memory, Some(latest));
        assert!(runner.app().memory_upload.is_none());
        assert!(runner.app().trouble.is_some());

        let commands = runner.action(action_id(super::RETRY_MEMORY));

        assert!(commands
            .iter()
            .any(|command| matches!(command, Command::Store(_))));
        assert!(runner.app().pending_memory.is_none());
        assert!(runner.app().memory_upload.is_some());
        assert!(runner.app().trouble.is_none());
    }

    #[test]
    fn failed_close_save_retains_its_in_flight_bytes_after_the_book_is_closed() {
        let in_flight = ("memory.ITEM1".to_owned(), vec![7; 300_000]);
        let mut runner = AppRunner::new(super::ReadingList {
            view: super::View::Detail,
            memory_upload: Some(ShelfUpload::new(in_flight.0.clone(), in_flight.1.clone())),
            active_memory: Some(in_flight.clone()),
            ..super::ReadingList::default()
        });

        runner.store_result(StoreResult::Denied(StoreError::TooFull));

        assert_eq!(runner.app().pending_memory, Some(in_flight));
        assert!(runner.app().active_memory.is_none());
        assert!(runner.app().memory_upload.is_none());
        assert!(runner.app().trouble.is_some());
    }
}
