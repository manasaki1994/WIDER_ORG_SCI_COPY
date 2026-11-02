#!/bin/zsh
# The paid run behind the interactive demo's screened checkpoints (design D3). Invented papers only.
#
#   zsh showcase/demo/build_demo_project.sh [OUT]      # OUT defaults to ~/wider-showcase-data/demo
#
# Writes OUT/screened (set 1: gpt-4o-mini, temperature 0, seed 1; the second setting already configured) and
# OUT/screened2 (set 2 added: gpt-4o-mini, temperature 0.7, seed 2). About 112 model calls per set, plus a few for
# the second synonyms proposal and the adjacent literatures (model proposals the demo's Free mode can show).
# The free checkpoints come from build_checkpoints.py, which also signs and completes copies of an older run (--paid).
#
# The key is read from the clipboard into this process only; the clipboard is emptied and the key is never written
# to disk or shell history. Run it from the repository root with the project's Python on PATH (or set PYTHON).
set -u
OUT=${1:-$HOME/wider-showcase-data/demo}
PY=${PYTHON:-python3}
ROOT=${0:A:h:h:h}
WORK=$(mktemp -d)
print "Copy your OpenAI key to the clipboard, then press Enter here."
read -r _
OPENAI_API_KEY=${$(pbpaste)//[[:space:]]/}
if [[ $OPENAI_API_KEY != sk-* ]]; then
  print "No OpenAI key on the clipboard; nothing started."
  exit 1
fi
print -n | pbcopy
print "Key received (clipboard emptied). Running; about 10 minutes."
# A fresh WIDER home, so nothing from your own projects is read; every change is signed Reviewer A.
export OPENAI_API_KEY WIDER_HOME=$WORK/home WIDER_REVIEWER="Reviewer A" PYTHONPATH=$ROOT
P=$WORK/reviews/employee-voice
cd $ROOT || exit 1
mkdir -p $OUT
$PY showcase/demo/make_demo_example.py $WORK/example || exit 1
$PY showcase/build_project.py $P --example $WORK/example --provider openai --model gpt-4o-mini < /dev/null || exit 1
$PY - $P/project.yaml <<'EOF'
import sys, yaml
p = sys.argv[1]; c = yaml.safe_load(open(p))
base = next(x for x in c["providers"] if x["name"] == "openai")
c["providers"].append(dict(base, name="openai-t07", temperature=0.7, seed=2))
open(p, "w").write(yaml.safe_dump(c, sort_keys=False))
EOF
$PY -m wider providers allow openai-t07 --for probe --for research --dir $P || exit 1
# set 1 as the demo's "screened" checkpoint: its model cards already list the second setting
rm -rf $OUT/screened; cp -R $P $OUT/screened
for step in "construct propose synonyms --note second-suggestion" "construct adjacent propose" "construct adjacent find"; do
  print "$ wider $step"; $PY -m wider ${=step} --dir $P < /dev/null || print "extra step failed: $step"
done
$PY -m wider screen protocol corpus --name main --provider openai-t07 --new-set \
  --set-label "gpt-4o-mini, temperature 0.7" --no-reuse --dir $P < /dev/null || exit 1
rm -rf $OUT/screened2; cp -R $P $OUT/screened2
rm -rf $WORK
print "Done: $OUT/screened and $OUT/screened2. The key is forgotten when this window closes."
