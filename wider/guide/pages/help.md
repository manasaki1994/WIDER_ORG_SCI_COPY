# Help

## Page role
- the keyboard keys, the page names and their commands, and the status marks

## What to do
1. Read **Page names and commands** to find the command that does a page's work.
2. Read **Keyboard** and **Status marks** when a key or a mark is unclear.

## Why it matters
- Every action on a page has a command, so a step can be run from a script and read back.
- Methods: why WIDER works this way (the page `#methods`, or `wider guide methods`) explains each method choice in plain words, then for auditors.

## What you need
- Nothing beyond the project.

## Done when
- Nothing to finish: use it when you need it.

## Ask WIDER
- Press **Ask WIDER** in the top bar, or pick the "Ask WIDER" tab beside "Guide" in the side panel. "Asking
  about:" names the page you are on (change it, or pick "WIDER in general"); with no name set yet, type it in "Your name"
  there (afterwards Change beside "Working as" in the top bar changes it); type a question and press **Ask**. The answer is drawn from WIDER's own documents (the guide, the methods and the
  glossary), with its sources under it; it can be wrong, so check them. From the command line:
  `wider assistant ask "<question>" [--page <page>]`.
- It reads the page's guide, WIDER's documents, your project's settings and stage status, and your earlier
  questions. It never reads a record, a label, a note or a model's decision, and it is not offered while a labelling queue shows records.
- It never acts: a Show link opens the page and points at the button, and you press it yourself; a draft is text for you to copy.
- Your conversation is kept on this computer, in the project's `assistant/` folder, one file per name, and never
  goes into a share bundle or replication package. **Forget this conversation** deletes yours
  (`wider assistant forget`).
- If an answer did not help, press **Didn't help** under it (`wider assistant unhelpful <turn>`);
  `wider assistant history --unanswered` lists those questions so WIDER's documents can be improved.
