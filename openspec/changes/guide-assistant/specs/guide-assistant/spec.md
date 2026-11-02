## ADDED Requirements

### Requirement: Ask a question in the Ask WIDER tab
The system SHALL offer in a project, in the side panel's tab "Ask WIDER", a labelled text box, "Ask" with the send notice, the lines "It explains from WIDER's documents and can be wrong: check the sources. What you type is sent as written: do not paste a record's text." and the reviewer's conversation. The CLI uses the same engine.
Surfaces: UI side panel tab "Ask WIDER", "Ask"; CLI `wider assistant ask "<question>" [--page <page>] [--json]`; `POST /api/assistant/ask`.

#### Scenario: A question about the Deduplication page
- **WHEN** the synthetic project uses the fake backend for query creation, and the researcher on Deduplication opens the Ask WIDER tab, types "What does Unsure do?" and presses "Ask"
- **THEN** the tab shows the question, then the answer with "Answered by fake (fake-model)", and the box is empty with the focus in it
- **AND** `wider assistant ask "What does Unsure do?" --page dedup --json` returns the same answer fields
Pinned by: `tests/unit/test_assistant_js_node.py::test_ask_box_renders_answer_as_text`, `tests/unit/test_cli_assistant.py::test_cli_assistant_ask_json`

#### Scenario: Waiting and stopping
- **WHEN** the backend takes longer than a moment to answer
- **THEN** the tab says "Waiting for fake (fake-model)…" with a "Stop" button, and "Ask" cannot be pressed again
- **AND** "Stop" cancels the run, keeps the question in the box and adds no turn, and the run's row in `store.sqlite` holds no question or answer text
Pinned by: `tests/unit/test_assistant_js_node.py::test_ask_stop_keeps_question`, `tests/unit/test_assistant_routes.py::test_job_row_holds_no_text`

#### Scenario: An empty question, a second question, an unknown page
- **WHEN** the box is empty, or a question is asked while another of the project is running, or `wider assistant ask "x" --page label` runs
- **THEN** "Ask" cannot be pressed and `wider assistant ask ""` exits with code 1 and "Type a question first."; the second question gets "Another question is running: wait for it or press Stop."; the unknown page exits with code 1 and the guide's unknown-page message
Pinned by: `tests/unit/test_assistant_js_node.py::test_ask_disabled_when_empty`, `tests/unit/test_assistant_routes.py::test_routes_assistant_one_at_a_time`, `tests/unit/test_cli_assistant.py::test_cli_assistant_exit_codes`

#### Scenario: A typed question survives leaving the page
- **WHEN** the researcher has typed a question in the Ask WIDER tab, then goes to another page, switches to the Guide tab and back, closes and reopens the panel, or changes "Asking about:"
- **THEN** the typed question and the conversation are still there
Pinned by: `tests/unit/test_assistant_js_node.py::test_ask_box_kept_through_leaving_page`

### Requirement: Guide and Ask WIDER are tabs of one side panel
The system SHALL show Guide and Ask WIDER as two tabs of one side panel on project pages. The top-bar buttons "Guide" and "Ask WIDER" open it on their tab, switch to it, or close it when it shows; `#ask` opens the Ask WIDER tab. Arrow keys, Home and End switch tabs. "Asking about:" follows the page shown and can be set to another guide page or "WIDER in general".
Surfaces: UI side panel tabs, top bar, "Asking about"; CLI `wider assistant ask --page <page>` (without it: WIDER in general).

#### Scenario: Opened from Deduplication
- **WHEN** the researcher on Deduplication presses the top-bar "Ask WIDER", then "Guide", then "Guide" again
- **THEN** the side panel first shows the Ask WIDER tab with "Asking about:" set to Deduplication and the focus in the box, and a question asked there is sent with page `dedup`; then the Guide tab shows the Deduplication guide; then the panel closes
- **AND** each button's `aria-expanded` is true only while the panel shows its tab
Pinned by: `tests/unit/test_assistant_js_node.py::test_ask_page_remembers_page_came_from`

#### Scenario: Switching tabs by keyboard
- **WHEN** the focus is on the selected "Guide" tab and the researcher presses the right arrow, then Home, then the left arrow
- **THEN** "Ask WIDER" is selected with its tab panel shown and the focus on it, then "Guide", then "Ask WIDER" again; only the selected tab has `aria-selected` true and tab index 0, and the panel opens next time on the tab chosen last
Pinned by: `tests/unit/test_assistant_js_node.py::test_panel_tabs_keys`

#### Scenario: WIDER in general
- **WHEN** the researcher changes "Asking about:" to "WIDER in general" and asks "What is WIDER for?"
- **THEN** the question is sent with page `general`, the context holds no page guide and the rules name no button, and the answer has no "Show" link
- **AND** `wider assistant ask "What is WIDER for?" --json` without `--page` gives the same fields, and `wider assistant history` lists the turn "about WIDER in general"
Pinned by: `tests/unit/test_assistant_js_node.py::test_ask_page_general`, `tests/unit/test_assistant_context.py::test_general_context_no_page_guide`, `tests/unit/test_cli_assistant.py::test_cli_ask_without_page_is_general`, `tests/unit/test_assistant_engine.py::test_general_answer_drops_show`

#### Scenario: Opened by its address, or with no project
- **WHEN** the researcher opens `#ask?about=dedup`, or opens `#ask` by reload with no page shown before it, or opens `#ask` with no project open
- **THEN** Deduplication, or Overview with "Asking about:" set to "WIDER in general", shows with the panel on the Ask WIDER tab, and the address becomes that page's; with no project Home shows and says "Open a project first: Ask WIDER uses the project's model."
Pinned by: `tests/unit/test_assistant_js_node.py::test_ask_page_cold_open_and_no_project`

### Requirement: One side panel at a time
The system SHALL show at most one side panel: while a record drawer lies over the page the side panel is hidden, and it comes back on its tab when the drawer closes; opening the side panel closes an open record drawer. Deduplication's pair view, held inside the page, leaves the side panel open.
Surfaces: UI side panel and record drawer; no CLI twin (the CLI has no panels).

#### Scenario: The drawer and the panel
- **WHEN** at 1440 px with the Guide tab open the researcher opens a record's drawer, closes it, opens it again and then presses "Ask WIDER"
- **THEN** the side panel hides while the drawer shows and returns on the Guide tab when it closes; then the drawer closes and the panel shows the Ask WIDER tab with the focus in the box
Pinned by: `tests/unit/test_shell_js_node.py::test_panel_and_drawer_one_at_a_time`

### Requirement: Not offered while labelling
The system SHALL hide the top-bar button "Ask WIDER" while a labelling queue of any mode is open on Samples and labelling, and show in the Ask WIDER tab, in place of the box and the conversation, a sentence saying why. The Guide tab stays.
Surfaces: UI top bar and side panel while a labelling queue is open; no CLI twin (the CLI has no open labelling queue).

#### Scenario: The labelling queue
- **WHEN** the labelling queue, the re-check queue or the second-reviewer queue shows records
- **THEN** there is no "Ask WIDER" in the top bar, the Guide tab shows the guide, and the Ask WIDER tab shows only "Ask WIDER is not offered while you label, so your labels stay your own."
- **AND** when the queue is closed the button returns, and the Ask WIDER tab still holds any question typed before
- **AND** on any other page, also with a queue still loaded on Samples and labelling, Ask WIDER is offered as usual
Pinned by: `tests/unit/test_assistant_js_node.py::test_ask_box_absent_while_queue_open`

### Requirement: Without a model the tab offers no box
The system SHALL show in the Ask WIDER tab, when the project has no backend set for query creation, in place of the box "Ask WIDER needs a model. Set one on Model for query creation." with a link to that page. Nothing is sent or stored. The CLI prints the same sentence and exits with code 1.
Surfaces: UI Ask WIDER tab; CLI `wider assistant ask`.

#### Scenario: No model set
- **WHEN** the blank project has no construct backend and the researcher opens the Ask WIDER tab on Missing abstracts
- **THEN** there is no box and no "Ask", and the sentence links to Model for query creation; the Guide tab still shows the Missing abstracts guide with its six answers
- **AND** `wider assistant ask "hello" --page enrich` prints the sentence, exits with code 1 and creates no `assistant/` folder
Pinned by: `tests/unit/test_assistant_engine.py::test_ask_no_model`, `tests/unit/test_assistant_js_node.py::test_no_model_sentence`

### Requirement: The assistant uses the project's model
The system SHALL answer with the project's query-creation backend, name it under every answer, and send each question through the same request loop, usage log and consent check as other model calls. The answer has a fixed shape: text, buttons to show, drafts and cited sections.
Surfaces: UI Ask WIDER tab; CLI `wider assistant ask`, `wider costs`; `POST /api/assistant/ask`.

#### Scenario: The construct model answers
- **WHEN** backend `a` is the default and `b` is set for `construct`, and a question is asked
- **THEN** the call goes to `b`, the answer says "Answered by b (<model>)", and the usage log gains one row for `b` with stage `assistant` and no text
Pinned by: `tests/unit/test_assistant_engine.py::test_ask_uses_construct_backend`

#### Scenario: A malformed or empty answer
- **WHEN** the reply does not match the answer shape after the request loop's repair attempt, or its answer text is empty
- **THEN** the tab and the CLI say in red "The model did not give a usable answer. Ask again, or try a larger model." and no turn is stored
Pinned by: `tests/unit/test_assistant_engine.py::test_ask_malformed_answer`

#### Scenario: The backend cannot be reached
- **WHEN** the construct backend does not answer
- **THEN** the tab and the CLI say in red that WIDER could not reach the backend, naming it, the question stays in the box, and no turn is stored
Pinned by: `tests/unit/test_assistant_engine.py::test_ask_backend_unreachable`

#### Scenario: A backend that cannot write text
- **WHEN** the construct backend is one that only classifies (for example Jev) and a question is asked
- **THEN** the tab and the CLI show in red the message other text tasks give for that backend, nothing is sent, and no turn is stored
Pinned by: `tests/unit/test_assistant_engine.py::test_ask_backend_without_text`

### Requirement: Answers come from WIDER's documents
The system SHALL answer how and why questions from the shipped guide pages, methods document and glossary, send only the best-matching sections, and list under the answer, each with an "Open" link, the sent sections the answer cites, or every section sent when it cites none. When none matches, it says so without a model call.
Surfaces: UI "Based on:" under the answer, "Open"; CLI "Based on:" lines with the command that prints each section; `--json` `sources`.

#### Scenario: A why question
- **WHEN** the researcher asks "Why is the validation set hidden until the freeze?" on Search results
- **THEN** the context holds the methods section on the validation split, and "Based on:" lists it with an "Open" link to that section of the methods page
- **AND** `wider assistant ask ... --json` names the same section id under `sources`, and the plain output prints `wider guide methods <topic>` beside it
Pinned by: `tests/unit/test_assistant_engine.py::test_ask_sources_open_and_cli_command`

#### Scenario: Nothing matches
- **WHEN** the question is "What is the capital of France?", with and without the fake embedding backend
- **THEN** no model is called, nothing is sent, and the answer is "WIDER's documents do not explain this." with a link to the help
Pinned by: `tests/unit/test_assistant_engine.py::test_ask_nothing_matches_no_call`

#### Scenario: A cited section that was not sent
- **WHEN** the answer cites a section id that was not in the context
- **THEN** the citation is dropped with the note "The answer cited a section it was not given; it is not shown."
Pinned by: `tests/unit/test_assistant_engine.py::test_answer_unsent_source_dropped`

### Requirement: Answers at the depth the question needs
The system SHALL search each methods layer (In short, Why, For auditors) as its own section, so a plain question finds the plain layer and a technical one the auditor layer, and offer "Simpler" and "More detail" under each answer to ask again with the plainer or deeper layers of the same topics.
Surfaces: UI "Simpler", "More detail" under an answer; CLI `wider assistant ask --depth simpler|detail`; JSON `depth`.

#### Scenario: A plain and a technical question
- **WHEN** "What does recall mean?" and "Which variance estimator gives the recall interval?" are asked
- **THEN** the first context's top section is the glossary entry or the "In short" layer of the recall topic, and the second's first methods section is that topic's "For auditors" layer (glossary entries are always sent first)
Pinned by: `tests/unit/test_assistant_retrieve.py::test_plain_and_technical_question_pick_layers`

#### Scenario: More detail
- **WHEN** the researcher presses "More detail" under the answer to "What does recall mean?"
- **THEN** the same question is asked again, its context holds the "Why" and "For auditors" layers of the topics that question matched, and `--depth detail --json` gives the same sources with `depth: "detail"`
Pinned by: `tests/unit/test_assistant_retrieve.py::test_depth_detail_layers`, `tests/unit/test_assistant_js_node.py::test_more_detail_resends_with_depth`

### Requirement: Sections found by words and by meaning
The system SHALL find sections by keywords with the glossary's synonyms and, when the project's embedding backend is downloaded and is local or has consent, rank them also by meaning. Keywords decide whether anything matches. Only the question and shipped text are embedded; no model is downloaded for it.
Surfaces: UI "Based on:", the keyword-only note and its "Allow" button; CLI `wider assistant ask --json` (`retrieval`); `wider assistant index`.

#### Scenario: Keywords alone
- **WHEN** the project has no embedding backend and the question says "sensitivity"
- **THEN** the recall sections are found through the glossary synonym "recall", and the JSON says `retrieval: "keywords"`
Pinned by: `tests/unit/test_assistant_retrieve.py::test_synonym_finds_recall`

#### Scenario: With an embedding model
- **WHEN** a local, downloaded embedding backend is the project default
- **THEN** the first question builds `assistant/index/<embedding>-<hash>.npy` from shipped text only, and the JSON says `retrieval: "keywords and meaning"`
Pinned by: `tests/unit/test_assistant_retrieve.py::test_index_built_once_and_reused`, `tests/unit/test_assistant_retrieve.py::test_index_holds_shipped_text_only`

#### Scenario: A question that names a topic
- **WHEN** the question is "How is the recall estimate computed from the weighted validation sample?", with or without an embedding model, and the meaning ranking puts the recall-target and stratified-sampling topics first
- **THEN** the sections sent start with the glossary entry "validation sample" the question names, and the methods sections sent are those of the recall-estimate topic, whose id words "recall estimate" the question names
Pinned by: `tests/unit/test_assistant_retrieve.py::test_named_topic_and_entries_stay_first`

#### Scenario: A what-next question on a page
- **WHEN** "what should i do now", which has no word keywords can match, is asked on the Model for query creation page
- **THEN** the sections sent start with that page's "What to do" and the Overview's "What to do", and the question reaches the backend instead of getting "WIDER's documents do not explain this."
- **AND** asked in general, "What do I do first in WIDER?" gets the Overview's "What to do" and the methods overview first
Pinned by: `tests/unit/test_assistant_retrieve.py::test_what_next_question_gets_the_pages_what_to_do`, `tests/unit/test_assistant_engine.py::test_what_next_on_a_page_reaches_the_model`

#### Scenario: Meaning does not push out the best keyword section
- **WHEN** the embedding backend (the fake one counts, as on the hand-check copy) ranks the best keyword section last
- **THEN** that section is still among the sections sent, after the named entries and topic
Pinned by: `tests/unit/test_assistant_retrieve.py::test_best_keyword_section_never_pushed_out_by_meaning`

#### Scenario: An embedding model not downloaded
- **WHEN** the default embedding backend is a sentence-transformers model whose weights are not on this computer
- **THEN** nothing is downloaded, keywords alone are used, and the note says "Searched by keywords only: the embedding model is not downloaded."
Pinned by: `tests/unit/test_assistant_retrieve.py::test_embedding_not_downloaded_falls_back_without_download`

#### Scenario: A cloud embedding backend without consent
- **WHEN** the default embedding backend is a remote service without consent for `assistant`
- **THEN** keywords alone are used with the note "Searched by keywords only: the embedding service needs your consent." and an "Allow <backend> for Ask WIDER…" button that opens the consent dialog
Pinned by: `tests/unit/test_assistant_retrieve.py::test_cloud_embedding_without_consent_falls_back`, `tests/unit/test_assistant_js_node.py::test_keyword_note_allow_opens_embedding_consent`

### Requirement: Edits to WIDER's documents take effect at once
The system SHALL answer each question from the guide and methods files as they are now: an edited sentence, context line or glossary synonym is used by the next question with no restart or step. Nothing from before the edit is reused. A methods source not reviewed now, or edited since its review, is marked, not hidden.
Surfaces: UI "Based on:", the conversation; CLI `wider assistant ask --json` (`unreviewed`), `history --json` (`docs_changed`).

#### Scenario: An edited topic is answered from the new text
- **WHEN** the first sentence of the first methods section sent for a question is replaced in its file under `wider/guide/methods/`, and the same question is asked again with no other step
- **THEN** the context sent holds the new sentence and not the old one, "Based on:" still names that section, and an answer quoting its first sentence quotes the new one
Pinned by: `tests/unit/test_assistant_engine.py::test_edited_topic_answered_from_new_text`

#### Scenario: An edited synonym is used
- **WHEN** a word found nowhere in WIDER's documents (for example "findability") finds no section, and is then added to the synonyms of the glossary entry "recall"
- **THEN** the next question with that word finds the recall sections by keywords, and with the fake embedding backend a new index file replaces the old one
Pinned by: `tests/unit/test_assistant_retrieve.py::test_edited_synonym_found_and_index_rebuilt`

#### Scenario: Earlier turns after an edit
- **WHEN** a section sent with one of this reviewer's earlier turns has since changed or gone, and another section has changed that no earlier turn was sent
- **THEN** only the turns that were sent the changed section are left out of the context, and the tab and `wider assistant history` mark each "Answered before WIDER's documents changed." (`docs_changed: true` in JSON); the other turns are sent and unmarked
Pinned by: `tests/unit/test_assistant_context.py::test_turns_before_edit_left_out`, `tests/unit/test_assistant_js_node.py::test_turn_marked_docs_changed`

#### Scenario: An edited reviewed topic
- **WHEN** a topic the author marked reviewed is edited and not reviewed again, and an answer is based on it
- **THEN** "Based on:" names it with "(not yet reviewed by the author)" in the tab and the CLI, and `--json` lists its section ids under `unreviewed`
Pinned by: `tests/unit/test_assistant_engine.py::test_edited_reviewed_topic_marked_unreviewed`

#### Scenario: A source never reviewed
- **WHEN** an answer is based on a methods topic or glossary entry whose status is "drafted"
- **THEN** it carries the same mark and is listed under `unreviewed`; a guide page section carries no mark
Pinned by: `tests/unit/test_assistant_engine.py::test_drafted_source_marked_unreviewed`

#### Scenario: A methods file that cannot be read
- **WHEN** a topic file is saved with a missing heading or broken front matter, and a question is asked
- **THEN** its sections are left out, the other documents answer, and the note says "WIDER's documents: <file> cannot be read (<problem>); it is left out until it is fixed."
Pinned by: `tests/unit/test_assistant_engine.py::test_ask_bad_methods_file_left_out_with_note`

### Requirement: Retrieval checked against a fixed question set
The system SHALL ship at least 15 questions each from a first-time user, a researcher and a statistician or auditor, each naming the section it needs, and 10 that WIDER's documents do not cover. With keywords alone, at least 90% of each group find their section in the first three; no uncovered one finds any.
Surfaces: test suite; CLI `wider assistant check-retrieval [--json]`; no UI twin (a developer and auditor check).

#### Scenario: The shipped set passes
- **WHEN** `wider assistant check-retrieval --json` runs on the shipped documents
- **THEN** it reports the share found in the first three per group, each at least 0.9, and 0 sections for the uncovered questions, and exits with code 0
Pinned by: `tests/unit/test_assistant_evaluate.py::test_question_set_retrieval`, `tests/unit/test_cli_assistant.py::test_cli_check_retrieval`

#### Scenario: The set through Ask WIDER's own ranking
- **WHEN** the shipped set is ranked as Ask WIDER ranks a question (named entries and topic, what-next, keywords fused with meaning), with the fake embedding backend as the project default
- **THEN** each group still finds its section in the first three at least 0.9 of the time, and a needed section of every covered question is among the sections sent
Pinned by: `tests/unit/test_assistant_retrieve.py::test_check_retrieval_through_fused_path_with_fake_embedder`

### Requirement: What the assistant reads
The system SHALL build the context in one function from this list, highest priority first: rules, question, this page's guide, matching sections, this project's settings for those sections, project status in the guide's words, and this reviewer's earlier turns. Each answer lists the parts read.
Surfaces: UI "Read:" line under each answer; CLI `wider assistant ask --json` (`context`).

#### Scenario: The parts named under the answer
- **WHEN** a question about deduplication is asked on Deduplication with two earlier turns
- **THEN** the line reads "Read: this page's guide, 2 sections, your project's dedup settings, project status, 2 earlier turns"
- **AND** the JSON lists each part with its name and size in characters, without its text
Pinned by: `tests/unit/test_assistant_context.py::test_read_line_names_parts`

### Requirement: What the assistant never reads
The system MUST NOT put into the context any record's title, abstract or text, a label, a model decision or read, a score, rank or stratum, sample membership, a note or reason about a record, a validation paper, a recall result, a key or secret, or another reviewer's conversation.
Surfaces: UI "Read:" line; CLI `wider assistant ask --json` (`context`).

#### Scenario: No record data
- **WHEN** the synthetic project has an incomplete development sample with labels, model reads, record notes, a corrected label with its reason, a validation sample and a recall estimate, and a question is asked on Samples and labelling with the fake backend keeping what it receives
- **THEN** the text sent holds no record title or abstract, label, decision, record note, correction reason, membership or recall figure, and says "development labels open" without a count
Pinned by: `tests/unit/test_assistant_context.py::test_context_holds_no_record_data`

#### Scenario: Validation papers hidden until the freeze
- **WHEN** a question is asked on Search results before the query is frozen, and again after
- **THEN** the first context holds no validation paper's title, DOI or count, the second may hold the coverage counts the page shows then and no paper's title or DOI
- **AND** neither holds a recall estimate, interval or "met / not met"
Pinned by: `tests/unit/test_assistant_context.py::test_context_validation_hidden_until_freeze`

### Requirement: This project's settings
The system SHALL let the assistant say how this project is set up for the matched topics: the protocol values the matched sections name, with their shipped descriptions, and the version, status and origin of the artifacts they name. Free-text notes are not sent.
Surfaces: UI "Read:" line ("your project's <topic> settings"); CLI `wider assistant ask --json` (`context`).

#### Scenario: Why this recall target
- **WHEN** the protocol sets the recall target to 0.95 and the researcher asks "Why is our target 95%?"
- **THEN** the context holds the recall-target section and the target value with its shipped description, and no protocol value the matched sections do not name
Pinned by: `tests/unit/test_assistant_context.py::test_settings_named_values_only`

#### Scenario: Artifact versions without their notes
- **WHEN** a question about the search query matches a section naming the synonyms artifact, whose version 3 is frozen with a note
- **THEN** the context says "synonyms version 3, frozen, written by hand" and holds no text of the note
Pinned by: `tests/unit/test_assistant_context.py::test_artifact_note_not_sent`

### Requirement: Context size for small models
The system SHALL keep the context within a token budget (characters ÷ 4): 3,000 by default on this computer or an unknown place, 12,000 for the network or a remote service, editable on both surfaces. Turns go first, then status, then lower sections; the best section stays. An Ollama backend gets a window that fits.
Surfaces: UI Settings "Ask WIDER" card, "Read:" line; CLI `wider assistant budget [N|--default]`, `wider assistant ask --json`.

#### Scenario: A small budget drops the oldest turns first
- **WHEN** the budget is 800 and the conversation holds six earlier turns
- **THEN** the context keeps rules, question, page guide and the best section, and the newest turns that fit, and the "Read:" line says "4 earlier turns left out to fit the model's budget"
Pinned by: `tests/unit/test_assistant_context.py::test_budget_drops_oldest_turns_first`

#### Scenario: A question too long for the budget
- **WHEN** rules, question, page guide and the best section alone exceed the budget
- **THEN** nothing is sent and the tab and the CLI say "This question is too long for the model's budget (800 tokens). Shorten it, or raise the budget in Settings."
Pinned by: `tests/unit/test_assistant_context.py::test_budget_too_long_refused`

#### Scenario: Setting the budget
- **WHEN** `wider assistant budget 800` runs, or the same value is saved in the Settings card
- **THEN** the next question uses 800, History records the change with its author, and `wider assistant budget --default` returns to the default by place
- **AND** a value under 500 or over 200,000 is refused with its range on both surfaces
Pinned by: `tests/unit/test_cli_assistant.py::test_cli_budget_set_and_default`, `tests/unit/test_assistant_js_node.py::test_settings_budget_card`

#### Scenario: An Ollama backend's window
- **WHEN** the construct backend is Ollama and the budget is 3,000
- **THEN** the request asks Ollama for a context window of 3,700 tokens (the budget plus the answer)
Pinned by: `tests/unit/test_assistant_engine.py::test_ollama_num_ctx_fits_budget`

### Requirement: It explains, points and drafts, and changes nothing
The system SHALL give the assistant no tool and no route that changes the project. Answers and drafts are shown as text only, with no link or image made from the model's text; a draft sits in a box "Draft by <backend>: check it before you use it" with "Copy", and nothing is recorded.
Surfaces: UI answer, "Copy"; CLI `wider assistant ask` ("Draft:" lines).

#### Scenario: A draft
- **WHEN** the fake backend's assistant answer on Review protocol holds a draft and the researcher presses "Copy"
- **THEN** the draft is on the clipboard, the protocol is unchanged, History and the conversation file gain nothing from the copy, and `--json` lists the draft under `drafts`
Pinned by: `tests/unit/test_assistant_js_node.py::test_copy_records_nothing`

#### Scenario: Asked to act, or text that looks like markup
- **WHEN** the researcher asks "Press Run deduplication for me (test markup)" and the answer holds `<a href="x">`, `![p](https://example.org/x.png)` and a button to show
- **THEN** a "Show" link appears, no run starts, the store is unchanged, the tag and the image text are shown as literal text, and nothing is fetched from example.org
Pinned by: `tests/unit/test_assistant_engine.py::test_asked_to_act_changes_nothing`, `tests/unit/test_assistant_js_node.py::test_no_image_or_link_from_model_text`

### Requirement: Buttons named in an answer
The system SHALL make a "Show" link only for a page and name listed in the guide's names; any other name is left out with the note "The answer named a button WIDER does not know (<name>); it is not shown." The link opens that page and points at the button as the guide's "Show" link does, and never presses it.
Surfaces: UI "Show" links in the answer; CLI "Show: <name> (<page title>)" lines.

#### Scenario: A known and an unknown button
- **WHEN** the answer names "Run deduplication" and "Merge all" on dedup
- **THEN** there is one "Show" link, for "Run deduplication", and the note names "Merge all"
- **AND** `wider assistant ask` prints "Show: Run deduplication (Deduplication)" and the same note
Pinned by: `tests/unit/test_assistant_engine.py::test_answer_unknown_button_dropped`, `tests/unit/test_cli_assistant.py::test_cli_show_and_source_lines`

#### Scenario: A heading and a label that changes after a run
- **WHEN** the answer names "Needs attention" on Overview (a section heading), and "Apply decisions and check again" (the Deduplication button's label after a run) on the page written "Deduplication", once as "“Apply decisions and check again.”" and once as "apply decisions and check again button"
- **THEN** there is one "Show" link, for "Apply decisions and check again" on dedup, and the note names "Needs attention" only
- **AND** no name the assistant may show is found on the pages only as a heading, and every label of a page's main top-bar button is among that page's names
Pinned by: `tests/unit/test_assistant_engine.py::test_show_heading_refused_state_label_kept`, `tests/unit/test_guide.py::test_assistant_buttons_are_controls`

#### Scenario: Show from the Ask WIDER tab
- **WHEN** an answer about Deduplication has a "Show" link for "Run deduplication" and the researcher presses it in the Ask WIDER tab on Overview
- **THEN** Deduplication opens with "Run deduplication" outlined and focused, the panel stays on the Ask WIDER tab, and no run starts
- **AND** when the button is not on that page now, the Ask WIDER tab says "Run deduplication is not on the page now."
Pinned by: `tests/unit/test_assistant_js_node.py::test_show_link_in_answer_focuses_never_clicks`

### Requirement: One conversation per reviewer
The system SHALL keep each reviewer's conversation apart, named by the reviewer the tab, `--by`, `$WIDER_REVIEWER` or `$USER` gives: the tab and history show only that reviewer's turns, and only they go into the context. The tab uses the one name the pages sign with. Without a name nothing is asked or stored.
Surfaces: UI "Asking as" or "Your name" and the conversation in the Ask WIDER tab; CLI `wider assistant history [--by NAME] [--json]`; `GET /api/assistant/history`.

#### Scenario: Two reviewers
- **WHEN** reviewer "ana" asks two questions and reviewer "ben" then asks one
- **THEN** ben's tab and `wider assistant history --by ben` show one turn, ana's show two, and ben's question was sent with no turn of ana's
Pinned by: `tests/unit/test_assistant_store.py::test_store_per_author`

#### Scenario: The same name in another case
- **WHEN** a reviewer asked as "Reviewer" and later asks as "reviewer" or " reviewer "
- **THEN** both go to one conversation, and a conversation kept apart before this (one file per exact name) is folded in, in the order the questions were asked, when either name is next used; a file whose turns were asked by another name stays apart
Pinned by: `tests/unit/test_assistant_store.py::test_store_per_author`, `tests/unit/test_assistant_store.py::test_store_folds_files_kept_by_exact_name`

#### Scenario: No reviewer name
- **WHEN** the tab sends no reviewer name, or the CLI finds none in `--by`, `$WIDER_REVIEWER` or `$USER`
- **THEN** nothing is sent or stored; "Ask" cannot be pressed and the tab says "Type your name in Your name above: your conversation is kept under your name.", and the CLI says "Enter your name first: your conversation is kept under your name."
Pinned by: `tests/unit/test_assistant_engine.py::test_ask_needs_author`, `tests/unit/test_assistant_js_node.py::test_ask_page_name_field`

#### Scenario: The name field
- **WHEN** the researcher with no name yet types "ana" in "Your name" in the Ask WIDER tab and leaves the field
- **THEN** ana's conversation shows without a reload and the other pages sign with "ana"
- **AND** the field gives way to "Asking as ana: your conversation is kept under this name.", followed, where the top bar shows "Working as", by "To change it, use Change beside Working as in the top bar."
Pinned by: `tests/unit/test_assistant_js_node.py::test_ask_page_name_field`

### Requirement: Forget a conversation
The system SHALL let a reviewer delete their own conversation after an in-page confirmation that says what is deleted; only that reviewer's file is deleted. The CLI asks the same unless `--yes`.
Surfaces: UI "Forget this conversation" and its dialog; CLI `wider assistant forget [--by NAME] [--yes]`; `DELETE /api/assistant/history`.

#### Scenario: Forget after confirming
- **WHEN** ana presses "Forget this conversation", reads "Deletes your 4 questions and answers on this computer. This cannot be undone." and confirms
- **THEN** ana's conversation is empty, ben's is unchanged, and History gains nothing
- **AND** `wider assistant forget --by ana` without `--yes` and without a terminal exits with code 2 and deletes nothing
Pinned by: `tests/unit/test_assistant_js_node.py::test_forget_dialog`, `tests/unit/test_assistant_store.py::test_forget_only_own`, `tests/unit/test_cli_assistant.py::test_cli_forget_needs_yes`

### Requirement: Questions the documents could not answer
The system SHALL let a reviewer mark an answer "Didn't help" and list, from their own conversation only, the questions that matched no section or were marked, so the documents can be improved. Nothing leaves the computer.
Surfaces: UI "Didn't help" under each answer; CLI `wider assistant unhelpful <turn>`, `wider assistant history --unanswered [--json]` (turns numbered from 1).

#### Scenario: The list of gaps
- **WHEN** ana asked one question that matched no section and marked another answer "Didn't help"
- **THEN** `wider assistant history --by ana --unanswered` lists both questions with their turn numbers and dates, and the usage log and History gain nothing
Pinned by: `tests/unit/test_assistant_store.py::test_unanswered_lists_unmatched_and_marked`, `tests/unit/test_cli_assistant.py::test_cli_history_unanswered`
