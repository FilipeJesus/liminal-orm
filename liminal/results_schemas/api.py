from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

from liminal.connection.benchling_service import BenchlingService
from liminal.utils import (
    EARLY_ACCESS_HEADER,
    MAX_CONCURRENT_REQUESTS,
    await_task_v3,
    list_all_items_v3,
)

_RESULT_SCHEMA_API_PATH = "/api/v3/result-schema"


def list_results_schemas(
    benchling_service: BenchlingService,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """Fetch result schemas from the v3 API."""
    return list_all_items_v3(
        benchling_service, f"{_RESULT_SCHEMA_API_PATH}/items", include_archived
    )


def list_result_schema_field_definitions(
    benchling_service: BenchlingService,
    result_schema_id: str,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """Fetch a result schema's field definitions from the v3 API.
    Archived fields are needed to write fields, since set-field-definitions rejects a list that leaves out any existing field."""
    return list_all_items_v3(
        benchling_service,
        f"{_RESULT_SCHEMA_API_PATH}/{result_schema_id}/field-definitions/items",
        include_archived,
    )


def attach_result_schema_field_definitions(
    benchling_service: BenchlingService,
    result_schema: dict[str, Any],
    include_archived: bool = False,
) -> dict[str, Any]:
    result_schema["fields"] = list_result_schema_field_definitions(
        benchling_service, result_schema["id"], include_archived
    )
    return result_schema


def list_results_schemas_with_fields(
    benchling_service: BenchlingService,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """Fetch all result schemas and their field definitions from the v3 API.
    include_archived applies to both the schemas and their fields."""
    result_schemas = list_results_schemas(benchling_service, include_archived)

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as executor:
        list(
            executor.map(
                partial(
                    attach_result_schema_field_definitions,
                    benchling_service,
                    include_archived=include_archived,
                ),
                result_schemas,
            )
        )

    return result_schemas


def create_results_schema(
    benchling_service: BenchlingService, payload: dict[str, Any]
) -> dict[str, Any]:
    """Create a new result schema."""
    response = benchling_service.api.post_response(
        url=_RESULT_SCHEMA_API_PATH,
        body=payload,
        additional_headers=EARLY_ACCESS_HEADER,
    )
    if not (200 <= response.status_code < 300):
        raise Exception("Failed to create result schema:", response.content)
    return json.loads(response.content)


def update_results_schema(
    benchling_service: BenchlingService,
    result_schema_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Update a result schema's name, system name or archived state."""
    response = benchling_service.api.patch_response(
        url=f"{_RESULT_SCHEMA_API_PATH}/{result_schema_id}",
        body=payload,
        additional_headers=EARLY_ACCESS_HEADER,
    )
    if not (200 <= response.status_code < 300):
        raise Exception("Failed to update result schema:", response.content)
    return json.loads(response.content)


def set_results_schema_field_definitions(
    benchling_service: BenchlingService,
    result_schema_id: str,
    field_definitions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replace a result schema's field definitions.
    The endpoint starts an async task, so this polls the task until it succeeds and returns the final task response."""
    response = benchling_service.api.post_response(
        url=f"{_RESULT_SCHEMA_API_PATH}/{result_schema_id}:set-field-definitions",
        body={"fieldDefinitions": field_definitions},
        additional_headers=EARLY_ACCESS_HEADER,
    )
    if not (200 <= response.status_code < 300):
        raise Exception(
            "Failed to set result schema field definitions:", response.content
        )
    task = json.loads(response.content)
    return await_task_v3(benchling_service, task["pollingUri"])
