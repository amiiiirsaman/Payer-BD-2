from payer_intel.config import get_settings
from payer_intel.llm import get_llm


def test_settings_loaded_from_env():
    s = get_settings()
    assert s.bedrock_model_id  # has a default
    assert s.aws_region
    assert s.searchapi_key
    assert s.search_provider == "searchapi"


def test_llm_targets_bedrock_sonnet():
    llm = get_llm()
    # CrewAI strips the "bedrock/" prefix into a separate .provider attribute
    assert getattr(llm, "provider", "") == "bedrock"
    assert "claude-sonnet-4-5" in getattr(llm, "model", "")
    assert llm.aws_access_key_id and llm.aws_secret_access_key
