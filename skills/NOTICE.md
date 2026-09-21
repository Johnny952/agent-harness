# Third-party notices

Every skill under `skills/` is derived from MIT-licensed work. This file
records where each one came from and what was changed, and reproduces the
upstream copyright notices as the MIT licence requires.

None of these skills is upstream verbatim except where noted. They were
pruned and rewritten for this harness: the roles here are non-interactive
agents in a dispatcher pipeline, so every instruction that assumed a human
sitting at the other end of the conversation was rewritten to route through
the task handoff instead. Bugs in the vendored copies are ours, not the
upstream authors'.

## Provenance

| Skill | Upstream | Source | Change |
|---|---|---|---|
| `systematic-debugging` | superpowers 6.3.0, `skills/systematic-debugging` | https://github.com/obra/superpowers | Pruned: cross-skill references hedged, the "ask your human partner" escalation rewritten as a `blocked` handoff, one example reference file dropped |
| `test-driven-development` | superpowers 6.3.0, `skills/test-driven-development` | https://github.com/obra/superpowers | Pruned: exceptions must be declared in the handoff rather than approved by a human |
| `verification-before-completion` | superpowers 6.3.0, `skills/verification-before-completion` | https://github.com/obra/superpowers | Verbatim |
| `writing-plans` | superpowers 6.3.0, `skills/writing-plans` | https://github.com/obra/superpowers | Pruned: worktree and plan-path conventions replaced with this harness's, the execution-handoff section rewritten |
| `receiving-code-review` | superpowers 6.3.0, `skills/receiving-code-review` | https://github.com/obra/superpowers | Pruned: reviewer sources remapped onto the revisor and auditor roles, clarification requests rewritten as handoff statements, the GitHub thread-reply section dropped |
| `blocking-review` | thermos, `skills/thermo-nuclear-review` | https://github.com/cursor/plugins/tree/main/thermos | Rewritten: the PR-comment workflow replaced with this harness's finding format and the dispatcher's `VERDICT:` contract |
| `code-quality-review` | thermos, `skills/thermo-nuclear-code-quality-review` | https://github.com/cursor/plugins/tree/main/thermos | Rewritten: the approval bar reframed as non-blocking findings, since the auditor runs after approval and gates nothing |
| `minimal-scope` | ponytail | https://github.com/DietrichGebert/ponytail | Rewritten: the intensity modes, session persistence and slash-command surface dropped; the "no test frameworks" rule replaced with an explicit deferral to `test-driven-development` |

## Licences

### superpowers

```
MIT License

Copyright (c) 2025 Jesse Vincent

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### thermos

```
MIT License

Copyright (c) 2026 Cursor

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### ponytail

```
MIT License

Copyright (c) 2026 DietrichGebert

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
