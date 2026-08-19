# Reading List

Reading List is a personal Cobalt app for one allowlisted Zotero collection.
It shows the newest saved papers, searches the cached metadata, reads abstracts,
asks an owner-run bridge to convert stored PDF attachments, and keeps up to 96
converted texts on the reader. It never accesses Google Scholar, publisher
accounts, or Zotero write endpoints.

This app is a managed built-in rather than a public Store package because its
credential is bound to one owner's exact bridge origin. Build the platform with
that bare HTTPS origin (no path or alternate port):

```sh
READING_LIST_ORIGIN=https://papers.example.com \
  cargo run -p kobo-cli -- package
```

The same value is compiled into the app's URLs and the runtime's independent
credential policy. A build without it remains safe but shows a configuration
screen and refuses every credentialed Reading List request.

Install the bridge token without putting it in source or shell arguments:

```sh
kobo secret set reading-list --device <address>
```

For host development:

```sh
READING_LIST_ORIGIN=https://papers.example.com \
  cargo run -p kobo-cli -- run --sim --app reading-list
cargo test -p kobo-reading-list
```

Converted HTML is capped at 768 KiB. Text, structure, reading position, and
annotations can be retained offline; figures are fetched one at a time through
the authenticated bridge and are intentionally not persisted in v1.
