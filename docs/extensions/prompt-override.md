# Override a Prompt Without Python

Prompt-only extensions contain a manifest plus a Prompt directory.  No Python
entrypoint is needed.  Each Prompt is listed in `index.yaml` with a stable id,
SemVer version, template file, and variables.

```yaml
api_version: 1
prompts:
  - id: com.example.prompts.system
    version: 1.0.0
    file: system.txt
    required_variables:
      - audience
profiles:
  - id: com.example.prompts.default
    version: 1.0.0
    slots:
      system:
        prompt_id: com.example.prompts.system
```

`system.txt` can contain ordinary `{variable}` placeholders:

```text
You are a careful decision assistant for {audience}.
Use evidence from the available tools before deciding.
```

Validate and inspect it offline:

```bash
alpha-arena extension validate path/to/prompt-extension
alpha-arena extension list path/to/prompt-extension
```

Required variables must be present when rendering.  Rendered content carries
the Prompt specification and a SHA-256 hash, so a trace can identify the exact
version used by a decision.
