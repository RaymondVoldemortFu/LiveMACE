from benchmark.agents import AgentBuildContext


class MinimalLLM:
    def complete(self, request):
        raise AssertionError("not called")


class MinimalTools:
    def call(self, name, arguments):
        raise AssertionError("not called")


class MinimalPrompts:
    def render(self, prompt_id, variables):
        raise AssertionError("not called")


class MinimalEvents:
    def emit(self, event):
        raise AssertionError("not called")


def test_build_context_accepts_minimal_frozen_public_ports():
    context = AgentBuildContext(
        llm=MinimalLLM(),
        tools=MinimalTools(),
        prompts=MinimalPrompts(),
        events=MinimalEvents(),
    )

    assert isinstance(context.llm, MinimalLLM)
    assert isinstance(context.prompts, MinimalPrompts)
