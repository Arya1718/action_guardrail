"""
Mock tool definitions (OpenAI-compatible function calling format) and
simulated execution functions. All side effects are mocked — nothing real
ever happens.
"""

DELETE_RECORDS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_records",
        "description": "Delete records from a database table by count",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Name of the table to delete from",
                },
                "record_count": {
                    "type": "integer",
                    "description": "Number of records to delete",
                },
            },
            "required": ["table", "record_count"],
        },
    },
}

SEND_EMAIL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_email",
        "description": "Send an email to a recipient",
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {
                    "type": "string",
                    "description": "Email address of the recipient",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line",
                },
                "body": {
                    "type": "string",
                    "description": "Email body text",
                },
            },
            "required": ["recipient", "subject", "body"],
        },
    },
}

READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read the contents of a file at a given path",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute file path to read",
                },
            },
            "required": ["path"],
        },
    },
}

TOOL_SCHEMAS = [DELETE_RECORDS_SCHEMA, SEND_EMAIL_SCHEMA, READ_FILE_SCHEMA]

TOOL_NAME_MAP = {s["function"]["name"]: s for s in TOOL_SCHEMAS}


# ── Mock execution functions ────────────────────────────────────────────


def execute_delete_records(table: str, record_count: int) -> str:
    return (
        f"[SIMULATED] Deleted {record_count} records from table '{table}'. "
        f"Nothing was actually deleted — this is a mock."
    )


def execute_send_email(recipient: str, subject: str, body: str) -> str:
    return (
        f"[SIMULATED] Email sent to {recipient} with subject '{subject}' "
        f"({len(body)} chars). No real email was dispatched."
    )


def execute_read_file(path: str) -> str:
    return (
        f"[SIMULATED] Read file '{path}':\n"
        f"--- begin simulated content ---\n"
        f"This is mock file content for {path}.\n"
        f"It contains pretend data for testing purposes.\n"
        f"--- end simulated content ---"
    )


EXECUTOR_MAP = {
    "delete_records": execute_delete_records,
    "send_email": execute_send_email,
    "read_file": execute_read_file,
}


def execute_tool(name: str, params: dict) -> str:
    fn = EXECUTOR_MAP.get(name)
    if fn is None:
        return f"[SIMULATED] Unknown tool '{name}' — no simulation available."
    return fn(**params)
