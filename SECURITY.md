# Security Policy

## Reporting a Vulnerability

If you believe you have found a security vulnerability in `muninn-py`, please report it privately rather than opening a public issue.

**Preferred:** use GitHub's [private vulnerability reporting](https://github.com/lgreene03/muninn-py/security/advisories/new) on this repository.

**Alternative:** email the maintainer listed in the repository's GitHub profile, with subject line `[muninn-py-security]`.

Please include:

- A description of the issue.
- Steps to reproduce.
- The affected version (`pip show muninn-py` or git SHA).
- The impact you believe it has.
- Any suggested mitigation, if known.

You will receive an acknowledgement within 7 days. We aim to publish a fix or mitigation within 30 days of a confirmed report, depending on severity.

## Scope

`muninn-py` is a research SDK that communicates with a Muninn server over HTTP. The realistic threat surface is small but not zero. In-scope reports include:

- Vulnerabilities that allow a malicious server response to compromise the SDK client (e.g. SSRF, unsafe deserialisation).
- Secrets or credentials inadvertently exposed in SDK output, logs, or error messages.
- Dependency vulnerabilities that materially affect users of `muninn-py` at runtime.

Out of scope:

- Security issues in the Muninn server itself — report those to [lgreene03/muninn](https://github.com/lgreene03/muninn/security).
- Findings that require local shell access to the researcher's workstation.
- Speculative reports without reproduction steps.
- Issues in third-party services the SDK optionally integrates with — report those to the relevant project.

## Supported Versions

`muninn-py` is pre-1.0. Only the latest release on PyPI and `main` branch are currently supported. Once 1.0 ships, the latest minor version will receive security fixes.

## Dependency Auditing

We use `pip-audit` / Dependabot for automated dependency scanning. If you spot a dependency with a known CVE, opening a public issue (rather than a private advisory) is acceptable if the CVE is already publicly known, as long as you include the CVE identifier and the specific `muninn-py` code path that exposes the risk.
