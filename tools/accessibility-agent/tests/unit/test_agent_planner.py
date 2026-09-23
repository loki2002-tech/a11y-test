import pytest
from accessibility_agent.agent.planner import AgentPlanner
from accessibility_agent.ai.llm_client import LLMResponse

# Mock accessibility tree — minimal valid structure AgentPlanner._summarize_ax_tree() accepts
MOCK_AX_TREE = {
    "role": "WebArea",
    "name": "Test Page",
    "children": [
        {"role": "button", "name": "Submit", "children": []},
    ],
}


class MockLLMClient:
    def __init__(self, response_text: str, provider: str = "mock"):
        self._response_text = response_text
        self._provider = provider

    @property
    def provider_name(self):
        return self._provider

    async def generate(self, prompt: str, system: str = "") -> LLMResponse:
        return LLMResponse(text=self._response_text, model="mock-model", prompt_tokens=10, completion_tokens=10)


@pytest.mark.asyncio
async def test_planner_disabled_returns_empty():
    """When provider is 'disabled', the planner must short-circuit and return []."""
    mock_llm = MockLLMClient("", provider="disabled")
    planner = AgentPlanner(llm_client=mock_llm)
    assert planner.is_enabled is False
    result = await planner.get_next_interactions(MOCK_AX_TREE, "<html></html>", already_clicked=set())
    assert result == []


@pytest.mark.asyncio
async def test_planner_extracts_selectors_from_json():
    """LLM returns clean JSON — planner extracts selector list."""
    mock_response = '{"selectors": ["#btn1", ".menu"]}'
    mock_llm = MockLLMClient(mock_response, provider="mock")
    planner = AgentPlanner(llm_client=mock_llm)

    assert planner.is_enabled is True
    result = await planner.get_next_interactions(
        MOCK_AX_TREE,
        "<html><button id='btn1'></button></html>",
        already_clicked=set(),
    )
    assert result == ["#btn1", ".menu"]


@pytest.mark.asyncio
async def test_planner_extracts_selectors_from_markdown_fenced_json():
    """LLM returns JSON wrapped in a markdown code fence — planner strips it cleanly."""
    mock_response = '```json\n{"selectors": ["#modal-trigger"]}\n```'
    mock_llm = MockLLMClient(mock_response, provider="mock")
    planner = AgentPlanner(llm_client=mock_llm)

    result = await planner.get_next_interactions(MOCK_AX_TREE, "<html></html>", already_clicked=set())
    assert result == ["#modal-trigger"]


@pytest.mark.asyncio
async def test_planner_limits_to_5_selectors():
    """Even if LLM returns 6 selectors, planner caps the response at 5."""
    mock_response = '{"selectors": ["#s1", "#s2", "#s3", "#s4", "#s5", "#s6"]}'
    mock_llm = MockLLMClient(mock_response, provider="mock")
    planner = AgentPlanner(llm_client=mock_llm)

    result = await planner.get_next_interactions(MOCK_AX_TREE, "<html></html>", already_clicked=set())
    assert len(result) == 5
    assert result == ["#s1", "#s2", "#s3", "#s4", "#s5"]


@pytest.mark.asyncio
async def test_planner_handles_invalid_json():
    """When LLM returns unstructured text instead of JSON, planner returns [] safely."""
    mock_response = 'Here are the selectors: ["#s1"]'
    mock_llm = MockLLMClient(mock_response, provider="mock")
    planner = AgentPlanner(llm_client=mock_llm)

    result = await planner.get_next_interactions(MOCK_AX_TREE, "<html></html>", already_clicked=set())
    assert result == []


@pytest.mark.asyncio
async def test_planner_filters_already_clicked():
    """Selectors that are in already_clicked must be excluded from the returned list."""
    mock_response = '{"selectors": ["#btn1", "#btn2", "#btn3"]}'
    mock_llm = MockLLMClient(mock_response, provider="mock")
    planner = AgentPlanner(llm_client=mock_llm)

    result = await planner.get_next_interactions(
        MOCK_AX_TREE,
        "<html></html>",
        already_clicked={"#btn1"},
    )
    # #btn1 was already clicked, so it must NOT appear in results
    assert "#btn1" not in result
    assert "#btn2" in result
    assert "#btn3" in result
