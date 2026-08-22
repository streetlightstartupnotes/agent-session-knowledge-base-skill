# Open-source release contract

The repository contains executable Skill logic and generic documentation only. It must not contain real sessions, generated knowledge, audit output, local registry state, machine-specific validation reports, private paths, contacts, credentials, cookies, binary bodies, or a person's facts.

Run `release-check` against the directory that will be published. It reports only finding type, relative file, and line number; it never prints the matched value. Add repeated `--deny-term` values for private names, organizations, project codenames, or identifiers known to the maintainer. Do not save those terms in scripts, fixtures, shell history committed to the repository, or CI configuration.

The included `.gitignore` is defense in depth, not proof of safety. Inspect the exact Git staging set before a commit. Real-sample smoke tests must use an explicit output root outside the source tree. Synthetic tests should construct credential/contact examples from fragments so the source itself remains clean.

The release checker fails closed on generated `audit`, `knowledge`, `review`, and test-output directories; session/state file extensions; common credential and contact patterns; private home paths; raw Base64; binary/unapproved files; oversized files; and symlinks. A passing scan cannot prove that arbitrary prose contains no sensitive business fact, so the deny-term pass and human review remain required.

Release copy must state the Python 3.9+ prerequisite and distinguish verified input formats, native execution hosts, and operating systems exercised with real end-to-end runs. Do not use “all Agents” or “cross-platform verified” as a release claim merely because an adapter contract or portable CLI exists.
