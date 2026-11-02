# Model for query creation

## Page role
- the language model that proposes concepts, synonyms, anchor sentences and the codebook

## What to do
1. Press **Find AI models on this computer**, or **Add an AI model by hand**.
2. Press **Choose** on the model you want, then press **Check <name>** to make a short test call.
3. Press **Use <name> for query creation**.

## Why it matters
- Query creation needs a model, and the choice decides where your question and the abstracts of your seed papers
  are sent: each model says where it runs before you choose.

## What you need
- A model on this computer (for example through Ollama), or an API key of a remote service named by its environment
  variable.

## Done when
- A model for query creation is set.

## Watch out
- A remote model sends your question and seed paper abstracts off this computer; WIDER asks before the first call.
