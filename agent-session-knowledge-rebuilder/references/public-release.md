# Public source release contract

The repository contains executable Skill logic and generic documentation only. It must not contain real sessions, generated knowledge, audit output, local registry state, machine-specific validation reports, private paths, contacts, credentials, cookies, binary bodies, or a person's facts.

Keep portable mechanisms separate from private configuration: no fixed user's
language, occupation, destinations, preferred apps, writing prohibitions or
standing permissions. Installation does not grant task-end write permission.
Exercise contrasting synthetic users and domains when changing behavior; retain
the result privately and state whether it is a script test, an independent Agent
scenario, or real-host evidence. None substitutes for the others.

Package only the two Skill folders, license, public documentation and invented
development tests. Do not copy the working directory wholesale. Scan the exact
staged package as well as source, verify its member list, and bind the archive to
the scanned bytes. Keep private reports and deny-term files outside it. A locally
prepared candidate is not a pushed commit, tag or published release.

For source changes, run the relevant unit tests and, when adapter handling changes,
the synthetic golden pack. The latter lives in the development repository, not
necessarily in an installed Skill. Public-source validation is not an extra gate
for an ordinary private Markdown update or read-only recall.

Describe the distribution accurately as source code published under a noncommercial limited license. Do not call it MIT-licensed or OSI open source, and do not let a source release imply permission to publish a user's sessions or generated knowledge.

Run `release-check` against the directory that will be published. It reports only finding type, relative file, and line number; it never prints the matched value. For private names, organizations, project codenames, or identifiers, prefer repeated `--deny-term-file` arguments that point to newline-delimited files outside the release root. This keeps the terms out of the repository and reduces command-line history exposure. `--deny-term` remains available for controlled use. Never save private deny terms in scripts, fixtures, committed shell history, or CI configuration.

The included `.gitignore` is defense in depth, not proof of safety. Inspect the exact Git staging set before a commit. Real-sample manifests, transcripts, hashes that identify private samples, and smoke outputs must stay in a user-held root outside the source tree. Synthetic tests should construct credential/contact examples from fragments so the source itself remains clean.

The one allowed session-like fixture pack is `tests/fixtures/golden/v1/manifest.json`, whose files are inline, invented, and marked `fixture_kind: public-synthetic`. It covers the seven declared adapters without representing any person or real project. `golden-check` must fail when a case leaves unknown/unsupported candidates, coverage gaps, or discovery errors. A passing public fixture is regression evidence only and never authorizes `compatibility_claim_updated` or a new host/system claim.

The release checker fails closed on generated `audit`, `knowledge`, `review`, and test-output directories; session/state file extensions; provider and generic/compound credential assignments; authorization/Cookie headers, Cookie assignments/jars, URI userinfo and private keys; contacts, private addresses, and current or foreign home-account paths; padded or unpadded standard/Base64URL, MIME-wrapped blocks with short tails, parameterized data URLs, other binary/unapproved files; oversized files; and symlinks. A passing scan cannot prove that arbitrary prose contains no sensitive business fact, so the deny-term pass and human review remain required.

Release copy must state the Python 3.9+ prerequisite and distinguish verified input formats, execution hosts, and operating systems exercised with real end-to-end runs. Do not use “all Agents” or “cross-platform verified” as a release claim merely because an adapter contract or portable CLI exists.

Review public prose for storage safety as well as code safety. A cloud-sync or shared-directory example must disclose upload, access, and retained-version risks; it must never be presented as the silent default for private knowledge.
