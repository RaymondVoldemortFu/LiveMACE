from database.connection import SessionLocal
from database.models import AgentTrace
import json
import ast

db = SessionLocal()

# Get all assistant traces with tool_calls
traces = db.query(AgentTrace).filter(
    AgentTrace.role == 'assistant',
    AgentTrace.tool_calls.isnot(None)
).all()

print(f'Checking {len(traces)} traces...\n')

memory_search_count = 0
memory_add_count = 0
tool_counts = {}

for trace in traces:
    try:
        # Parse outer JSON array
        tool_calls_list = json.loads(trace.tool_calls)

        if isinstance(tool_calls_list, list):
            for tool_call_str in tool_calls_list:
                # Parse inner Python dict string
                if isinstance(tool_call_str, str):
                    tool_call = ast.literal_eval(tool_call_str)
                else:
                    tool_call = tool_call_str

                tool_name = tool_call.get("function", {}).get("name")

                # Count all tools
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

                if tool_name == "memory_search":
                    memory_search_count += 1
                    print(f'Found memory_search:')
                    print(f'  trace_id: {trace.trace_id}')
                    print(f'  step: {trace.step_number}')
                    print(f'  created_at: {trace.created_at}')
                    print(f'  arguments: {tool_call.get("function", {}).get("arguments")}')
                    print()
                elif tool_name == "memory_add":
                    memory_add_count += 1
                    print(f'Found memory_add:')
                    print(f'  trace_id: {trace.trace_id}')
                    print(f'  step: {trace.step_number}')
                    print(f'  created_at: {trace.created_at}')
                    print()
    except Exception as e:
        continue

print(f'=== Summary ===')
print(f'Total traces checked: {len(traces)}')
print(f'memory_search calls: {memory_search_count}')
print(f'memory_add calls: {memory_add_count}')
print(f'\nAll tools used:')
for tool, count in sorted(tool_counts.items(), key=lambda x: x[1], reverse=True):
    print(f'  {tool}: {count}')

db.close()
