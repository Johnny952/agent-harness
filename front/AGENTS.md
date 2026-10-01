<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Project rules

- All backend reads/writes go through the typed client in `src/lib/api/client.ts` (one function per endpoint), currently backed by `src/lib/api/mock/fixtures.ts` — swapping to the real REST backend is a single-file change.
- Query keys and poll intervals live in `src/lib/api/queries.ts`; nothing polls faster than 2s.
- No authentication of any kind: the console runs on a private network behind one trusted operator.
- Every screen renders inside `AppShell` (`src/components/console/app-shell.tsx`), which owns navigation, keyboard shortcuts and the chat dock.
