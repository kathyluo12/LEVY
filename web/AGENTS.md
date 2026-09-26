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

## Architecture

- Keep LEVY frontend-only and deterministic for the demo; isolate typed mock data in `src/lib` so a later external intelligence API can replace it without changing the interface.
- MongoDB Atlas lives behind a separate agent service; this app reads it only via `GET {LEVY_API_URL}/v1/snapshot` (Bearer `LEVY_API_KEY`) in `src/lib/levy-source.server.ts`, falling back to demo data — Atlas Data API is retired and direct drivers are unreliable on this runtime.
