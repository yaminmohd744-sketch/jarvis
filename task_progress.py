"""Per-request checklist and deterministic completion report."""

TASK_SCHEMAS = [
    {"type": "function", "function": {
        "name": "plan_task", "description": "Before a multi-step task, list every requested outcome in order. Call once, then execute the plan.",
        "parameters": {"type": "object", "properties": {
            "steps": {"type": "array", "items": {"type": "string"}, "minItems": 1}
        }, "required": ["steps"]}}},
    {"type": "function", "function": {
        "name": "update_task", "description": "Record a step's outcome. Completed steps require a successful action tool call ID as evidence. Blocked steps require the reason. Continue other independent steps.",
        "parameters": {"type": "object", "properties": {
            "step": {"type": "integer", "description": "One-based step number."},
            "status": {"type": "string", "enum": ["completed", "blocked"]},
            "detail": {"type": "string", "description": "Specific result (include relevant output or file location) or blocker."},
            "evidence_call_id": {"type": "string", "description": "Successful action tool call ID; required for completed steps."}
        }, "required": ["step", "status", "detail"]}}},
]


class TaskProgress:
    def __init__(self):
        self.steps = []
        self.evidence = set()

    @property
    def pending(self):
        return any(s['status'] == 'pending' for s in self.steps)

    def record(self, call_id, result):
        if isinstance(result, dict) and not result.get('error') and result.get('status') not in {
            'error', 'unknown', 'not_found', 'still_running'
        }:
            self.evidence.add(call_id)

    def handle(self, name, args):
        if name == 'plan_task':
            steps = args.get('steps')
            if self.steps:
                return {'error': 'A plan already exists. Update its steps; do not replace it.'}
            if not isinstance(steps, list) or not steps or not all(isinstance(s, str) and s.strip() for s in steps):
                return {'error': 'Provide a nonempty list of step descriptions.'}
            self.steps = [{'task': s, 'status': 'pending', 'detail': ''} for s in steps]
        else:
            step, status, detail = args.get('step'), args.get('status'), args.get('detail')
            if type(step) is not int or not 1 <= step <= len(self.steps):
                return {'error': 'Use an existing one-based step number.'}
            if status not in {'completed', 'blocked'} or not isinstance(detail, str) or not detail.strip():
                return {'error': 'Provide completed/blocked status and a specific result or blocker.'}
            if status == 'completed' and args.get('evidence_call_id') not in self.evidence:
                return {'error': 'Completion needs a successful action tool call ID. Execute and verify the step first.'}
            self.steps[step - 1].update(status=status, detail=detail)
        return {'steps': [{'step': i, **s} for i, s in enumerate(self.steps, 1)]}

    def report(self, reason=''):
        lines = [reason] if reason else []
        for status, heading in [('completed', 'Completed'), ('blocked', 'Blocked'), ('pending', 'Not completed')]:
            items = [s for s in self.steps if s['status'] == status]
            if items:
                lines.append(heading + ':')
                lines.extend(f"- {s['task']}" + (f": {s['detail']}" if s['detail'] else '') for s in items)
        return '\n'.join(lines)
