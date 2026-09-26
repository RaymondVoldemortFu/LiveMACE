# LiveMACE bench Extension SDK

The extension surface is a small, synchronous SPI.  An extension directory is
catalogued from `alpha-arena-extension.yaml`; it may contain an Agent, Tools,
Prompt files, or any combination of the three.

The four runnable examples are in `examples/extensions/`:

- `minimal-agent`: the smallest synchronous Agent.
- `read-only-tool`: a market Tool that requests only `market.read`.
- `prompt-override`: a Prompt-only extension with no Python component.
- `combined-extension`: an Agent, a Tool, and a Prompt profile together.

From the repository root, validate and test one example with:

```bash
cd backend
uv run alpha-arena extension validate ../examples/extensions/minimal-agent
uv run alpha-arena extension test ../examples/extensions/minimal-agent
uv run alpha-arena extension list ../examples/extensions/minimal-agent
```

The same commands are available through the installed `alpha-arena` entry
point.  `list` performs only local manifest/schema/Prompt-index checks; it does
not start a scheduler or connect to a service.

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
