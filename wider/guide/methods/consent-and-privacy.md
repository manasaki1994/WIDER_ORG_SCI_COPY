---
id: consent-and-privacy
title: Consent and what leaves the computer
context: Before any step sends text to a model or an online service, from building the search to screening, WIDER says what goes where and asks your consent for anything that is not on this computer.
settings: []
artifacts: []
pages: [settings, model]
---

## In short

WIDER runs on your computer and your project files stay there. Some steps use a language model, an embedding model or an online service. Before such a step starts, its button or command says what would be sent, where and why. A model on this computer needs no permission. A model elsewhere, on your network, in the cloud or in an unknown place, is contacted only after you allow it, separately for listing its models, a test question, research data, passages of seed PDFs and questions to Ask WIDER. Online lookups have their own switch. If its address, key variable or sign-in method changes, you are asked again. Withdraw consent in Settings.

## Why

A model's name rarely tells where text is processed, so WIDER judges the backend itself: the host of its address, the kind of backend and, for Ollama, whether the model is routed to a cloud service. A place that cannot be established counts as unknown and is treated like the cloud.

Consent is given per backend and per operation, because a quick test is not the same as sending titles and abstracts. It is tied to a fingerprint of the destination, so a changed address, key variable or header voids it. LLM backends, embedding backends and online lookups each keep their own consent.

Ask WIDER, the guide panel's assistant, has its own consent. It sends your question, the page's guide, matching sections of WIDER's documents, the related settings, the project's status and your earlier questions, never a record, label or decision. With "Remember my choice for Ask WIDER" unticked, one question is allowed and neither the consent nor the question and answer is stored. An embedding backend searching WIDER's documents by meaning receives only your question and those documents.

The page and the CLI take the sentence on what is sent from one registry. WIDER stores only the name of the variable holding a key, and shared outputs are scanned for key values and home paths before they are written.

WIDER does not judge a provider's terms, and it sends no telemetry. A local Ollama address that does not answer needs no consent, since nothing can leave.

## For auditors

Locality is a classification of the configured endpoint, not a network trace: `wider/core/summary.py:host_class` reads the URL without resolving names, and `wider/core/summary.py:provider_locality` maps it to local, network, cloud, test or unknown. Ollama routing comes from the loopback tag list (`wider/core/detect.py:effective_locality`). A host name that resolves elsewhere, a proxy or a CLI agent that calls out on its own can defeat the classification.

`wider/core/backend_consent.py:assess` builds the fingerprint, a sha256 over kind, endpoint, locality, key variable name, auth, header names and a text version (plus model and fields for embeddings). `wider/core/backend_consent.py:require_backend_consent` raises `wider/core/backend_consent.py:BackendConsentRequired` before any request when the stored fingerprint or operation does not match; `wider/core/backend_consent.py:allow_backend` stores consent with author and time in `project.yaml`, a journalled settings change; `wider/core/backend_consent.py:revoke_backend` withdraws it. A question allowed without remembering passes through `wider/core/backend_consent.py:allow_once`, which holds for that one request and only while the fingerprint still matches; nothing is written. How much one question sends is capped by `wider/assistant/context.py:budget`: 3,000 tokens on this computer, 12,000 elsewhere, or `assistant.context_tokens` set in Settings or with `wider assistant budget`. Online lookups are gated by `wider/core/lookups.py:require_consent`.

`wider/core/send_notice.py:ACTIONS` lists every sending action and the modules that send for it; `wider/core/send_notice.py:notice` builds the sentence. Shared files are checked by `wider/core/sanitize.py:scan_for_secrets`.
