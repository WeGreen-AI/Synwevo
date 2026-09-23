"""Platform layer: the account service that other applications authenticate against.

Lives inside the DocWeave repository on purpose (see docs/architecture/platform.md §1): moving
the business application into apps/docweave while simultaneously introducing an identity
provider would change database paths, static bases, import paths and startup all at once, and
that combination is the usual cause of "the user data is gone".

The boundary is enforced by imports rather than by directory nesting: this package may use
`docweave.identity`, `docweave.sessions`, `docweave.passwords` and `docweave.store` — the single
account authority — and must not reach into DocWeave's business modules (`services`, `views`,
`jobs`, `engine`).
"""
