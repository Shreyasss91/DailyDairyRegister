# Office data — record_A

This folder holds office-specific working data for one office.

- Copy selected source templates from `data/templates/` into this folder; edit only the copies.
- Keep original source documents, notifications, approvals and extraction provenance available.
- **Files in this folder are no longer excluded by Git.** Git can track them when they are explicitly staged; this change does not itself add or publish any office data files.
- This repository is public. Any file committed here is visible to the public. Review every file and its full contents before staging or committing it.
- Do not use broad staging such as `git add .` or `git add data/record_A/` without first reviewing `git status` and the complete diff.
- Do not commit employee or consumer personal data, confidential office records, credentials, access tokens, databases, backups, or material whose publication has not been authorized by the relevant data owner/organization. When uncertain, keep it outside the repository.
- A successful application import or operator approval does not by itself authorize publishing the source file to a public Git repository.

Read `data/templates/README.md` before preparing files. It is the detailed data dictionary and explains supported importer datasets, source caveats, scheduling rules and capture-only boundaries.
