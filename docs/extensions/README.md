# LiveMACEBench Extension SDK

The extension surface is a small, synchronous SPI.  An extension directory is
catalogued from `livemace-bench-extension.yaml`; it may contain an Agent, Tools,
Prompt files, or any combination of the three.

The four runnable examples are in `examples/extensions/`:

- `minimal-agent`: the smallest synchronous Agent.
- `read-only-tool`: a market Tool that requests only `market.read`.
- `prompt-override`: a Prompt-only extension with no Python component.
- `combined-extension`: an Agent, a Tool, and a Prompt profile together.

From the repository root, validate and test one example with:

```bash
cd backend
uv run livemace-bench extension validate ../examples/extensions/minimal-agent
uv run livemace-bench extension test ../examples/extensions/minimal-agent
uv run livemace-bench extension list ../examples/extensions/minimal-agent
```

The same commands are available through the installed `livemace-bench` entry
point.  `list` performs only local manifest/schema/Prompt-index checks; it does
not start a scheduler or connect to a service.

## Runtime configuration

| Setting | Purpose |
| --- | --- |
| `LIVEMACE_BENCH_EXTENSION_DIRS` | Colon-separated extension directories |
| `LIVEMACE_BENCH_DISABLED_EXTENSIONS` | Comma-separated extension IDs to disable |
| `LIVEMACE_BENCH_ALLOWED_CAPABILITIES` | Comma-separated capability allowlist; defaults to all known capabilities |

When upgrading an existing checkout, run `uv sync` from `backend/` to refresh
the installed CLI. Name each custom extension manifest
`livemace-bench-extension.yaml` and use the environment variables above.

Built-in components and third-party extensions use the same Catalog loading
path. The v1 SPI is synchronous: extensions may manage asyncio or threads
inside a synchronous method, but the runtime does not schedule, await, or
supervise those resources.

## Guides

- [Create an Agent in ten minutes](10-minute-agent.md)
- [Add a read-only Tool](read-only-tool.md)
- [Override a Prompt without Python](prompt-override.md)
- [Contract tests and fake ports](contract-tests.md)
- [Public SPI, versions, and capabilities](public-spi.md)
- [Complete v1 interface reference](interface-reference.md)
