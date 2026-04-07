# Debug test
import sys
import json
sys.path.insert(0, 'e:/LLM-TTRA/test-agent/railway_dispatch')

from models.data_loader import get_trains_pydantic, get_stations_pydantic, use_real_data
from models.workflow_models import AccidentCard, NetworkSnapshot, DispatchContextMetadata
from railway_agent.llm_workflow_engine import layer2_planner

# Initialize data
use_real_data(True)
trains = get_trains_pydantic()[:10]
stations = get_stations_pydantic()

# Create accident_card directly
accident_card = AccidentCard(
    fault_type="大风",
    scene_category="临时限速",
    start_time=None,
    expected_duration=10.0,
    affected_section="SJP-SJP",
    location_type="station",
    location_code="SJP",
    location_name="石家庄",
    is_complete=True,
    missing_fields=[],
    affected_train_ids=["G1563"]
)

network_snapshot = NetworkSnapshot(
    train_count=1,
    station_count=13,
    solving_window={
        "observation_corridor": "SJP-SJP",
        "window_start": "2024-01-15T10:00:00",
        "window_end": "2024-01-15T12:00:00"
    }
)

dispatch_metadata = DispatchContextMetadata(
    train_count=1,
    station_count=13,
    time_window_start="2024-01-15T10:00:00",
    time_window_end="2024-01-15T12:00:00"
)

# Call layer2 directly and debug
from railway_agent.llm_workflow_engine import get_llm_caller, LAYER2_PROMPT, safe_json_dumps, get_rag_retriever

llm = get_llm_caller()

base_prompt = LAYER2_PROMPT.format(
    accident_card=safe_json_dumps(accident_card.model_dump()),
    network_snapshot=safe_json_dumps(network_snapshot.model_dump()),
    dispatch_context=safe_json_dumps(dispatch_metadata.model_dump())
)

query_for_rag = f"场景类型:{accident_card.scene_category},故障类型:{accident_card.fault_type}"
rag = get_rag_retriever()
prompt = rag.format_prompt_with_knowledge(base_prompt, query_for_rag)

print("Prompt:", prompt[:500])
print("\n---")

response = llm.call(prompt, max_tokens=512)
print("LLM Response:", response)
print("\n---")

# Parse the response manually
json_str = response
if '```json' in response:
    json_str = response.split('```json')[1].split('```')[0]
elif '```' in response:
    json_str = response.split('```')[1].split('```')[0]
elif '{' in response:
    start = response.find('{')
    end = response.rfind('}')
    if start >= 0 and end > start:
        json_str = response[start:end+1]

print("JSON string:", json_str)
print("\n---")

result = json.loads(json_str)
print("Parsed result keys:", result.keys())
print("result.get('主技能'):", result.get("主技能"))
print("result.get('skill_dispatch'):", result.get("skill_dispatch"))

if "主技能" in result:
    print("'主技能' is in result - TRUE")
else:
    print("'主技能' is in result - FALSE")